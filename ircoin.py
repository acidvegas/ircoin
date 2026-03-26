#!/usr/bin/env python3

import asyncio
import datetime
import json
import os
import random
import time

SERVER  = 'irc.supernets.org'
PORT    = 6697
USE_SSL = True
CHANNEL = '#superbowl'

DATA_FILE       = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ircoin_data.json')
SYNC_INTERVAL   = 300
JITTER_INTERVAL = 10
NAMES_INTERVAL  = 60

LINE_VALUE      = 0.001
RECENCY_MULT    = 3.0
BASE_COIN_PRICE = 0.001

MINE_BASE_DIFF  = 3
MINE_DIFF_SCALE = 0.1
MINE_DIFF_EXP   = 0.6
MINE_REWARD_MAX = 20.0
MINE_REWARD_MIN = 1.0
MINE_HALVING    = 200

STARTING_USD    = 1000.0
MINER_CUT       = 0.2
TRADE_IMPACT    = 0.5
PRESSURE_DECAY  = 0.995

B   = '\x02'
C   = '\x03'
GRN = '\x0303'
RED = '\x0304'
GRY = '\x0314'
YEL = '\x0308'
ORG = '\x0307'
CYN = '\x0311'
LGN = '\x0309'
LGY = '\x0315'
PNK = '\x0313'
BLU = '\x0312'

RESERVED = {'bal', 'top', 'help', 'market', 'rich', 'portfolio', 'staking', 'stake', 'give', 'news'}


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

def load_data() -> dict:
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r') as f:
            data = json.load(f)
    else:
        data = {}
    data.setdefault('lines', {})
    data.setdefault('minted', {})
    data.setdefault('available', {})
    data.setdefault('wallets', {})
    data.setdefault('usd', {})
    data.setdefault('jitter', 1.0)
    data.setdefault('pressure', {})
    data.setdefault('names', [])
    data.setdefault('staking_coins', [])
    data.setdefault('staking_last_calc', 0)
    data.setdefault('hourly_prices', [])
    data.setdefault('drop_announced', {})
    data.setdefault('news_headlines', [])
    data.setdefault('news_last_gen', 0)
    data.setdefault('news_boost', {})
    return data


def save_data(data: dict):
    tmp = DATA_FILE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(data, f)
    os.replace(tmp, DATA_FILE)


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def fmt_num(n) -> str:
    if isinstance(n, float):
        return f'{n:,.2f}'
    return f'{n:,}'

def fmt_price(p: float) -> str:
    if p >= 1.0:
        return f'${p:,.2f}'
    if p >= 0.01:
        return f'${p:.4f}'
    return f'${p:.6f}'

def fmt_coins(c: float) -> str:
    if c >= 100:
        return f'{int(c):,}'
    if c >= 1:
        return f'{c:,.2f}'
    return f'{c:.4f}'


# ---------------------------------------------------------------------------
# Hourly tracking
# ---------------------------------------------------------------------------

def hour_key() -> str:
    return str(int(time.time()) // 3600 * 3600)


def total_lines(data: dict, nick: str) -> int:
    return sum(data['lines'].get(nick.lower(), {}).values())


# ---------------------------------------------------------------------------
# Price engine
# ---------------------------------------------------------------------------

def coin_price(data: dict, nick: str) -> float:
    nl = nick.lower()
    hours = data['lines'].get(nl, {})
    if not hours:
        return BASE_COIN_PRICE * data.get('jitter', 1.0)

    now = time.time()
    month_ago = now - 30 * 86400
    recent = 0
    older = 0
    for ts_str, count in hours.items():
        if int(ts_str) >= month_ago:
            recent += count
        else:
            older += count

    weighted = recent * RECENCY_MULT + older
    price = BASE_COIN_PRICE + weighted * LINE_VALUE
    price *= data.get('jitter', 1.0)
    pressure = data.get('pressure', {}).get(nl, 0.0)
    price *= max(0.1, 1.0 + pressure)
    boost = data.get('news_boost', {}).get(nl, {})
    if boost and boost.get('expires', 0) > time.time():
        price *= max(0.1, 1.0 + boost['pct'] / 100)
    return max(price, BASE_COIN_PRICE * 0.5)


def mine_difficulty(data: dict, nick: str) -> int:
    minted = data['minted'].get(nick.lower(), 0.0)
    return max(MINE_BASE_DIFF, int(MINE_BASE_DIFF + minted ** MINE_DIFF_EXP * MINE_DIFF_SCALE))


def mine_reward(data: dict, nick: str) -> float:
    minted = data['minted'].get(nick.lower(), 0.0)
    base = random.uniform(MINE_REWARD_MIN, MINE_REWARD_MAX)
    return round(base / (1 + minted / MINE_HALVING), 4)


def apply_pressure(data: dict, nick: str, usd_amount: float, is_buy: bool):
    nl = nick.lower()
    minted = data['minted'].get(nl, 0.0)
    price = coin_price(data, nl)
    mcap = max(minted * price, 0.01)
    delta = (usd_amount / mcap) * TRADE_IMPACT
    if not is_buy:
        delta = -delta
    data['pressure'][nl] = data.get('pressure', {}).get(nl, 0.0) + delta


def tick_jitter(data: dict):
    jf = data['jitter']
    jf *= 1.0 + random.uniform(-0.02, 0.02)
    jf = jf * 0.95 + 1.0 * 0.05
    data['jitter'] = jf

    for nl in list(data.get('pressure', {}).keys()):
        data['pressure'][nl] *= PRESSURE_DECAY
        if abs(data['pressure'][nl]) < 0.0001:
            del data['pressure'][nl]

    # Staking interest accrual
    staking_rates = {e['coin']: e['rate'] for e in data.get('staking_coins', [])}
    if staking_rates:
        tick_days = JITTER_INTERVAL / 86400
        for wallet in data['wallets'].values():
            for coin in list(wallet.keys()):
                if coin in staking_rates and wallet[coin] > 0:
                    wallet[coin] += wallet[coin] * (staking_rates[coin] / 100) * tick_days

    cutoff = str(int(time.time() - 180 * 86400) // 3600 * 3600)
    for nick in list(data['lines'].keys()):
        trimmed = {k: v for k, v in data['lines'][nick].items() if k >= cutoff}
        if trimmed:
            data['lines'][nick] = trimmed
        else:
            del data['lines'][nick]


def recalc_staking(data: dict) -> list[dict]:
    holder_counts: dict[str, int] = {}
    for wallet in data['wallets'].values():
        for coin, amount in wallet.items():
            if amount > 0:
                holder_counts[coin] = holder_counts.get(coin, 0) + 1

    top = sorted(holder_counts.items(), key=lambda x: x[1], reverse=True)[:10]
    staking = []
    for i, (coin, holders) in enumerate(top):
        rate = round(3.0 - (i * 2.5 / 9), 2)
        staking.append({'coin': coin, 'holders': holders, 'rate': rate})
    data['staking_coins'] = staking
    data['staking_last_calc'] = time.time()
    return staking


def snapshot_prices(data: dict):
    prices = {}
    for nick in data['lines']:
        prices[nick] = coin_price(data, nick)
    data['hourly_prices'].append({'ts': time.time(), 'prices': prices})
    data['hourly_prices'] = data['hourly_prices'][-25:]


def check_price_drops(data: dict) -> list[tuple[str, float, float, float]]:
    snapshots = data.get('hourly_prices', [])
    if len(snapshots) < 2:
        return []
    now = time.time()
    old = None
    for s in snapshots:
        if s['ts'] <= now - 82800:
            old = s
            break
    if old is None:
        old = snapshots[0]
    if now - old['ts'] < 72000:
        return []

    drops = []
    announced = data.get('drop_announced', {})
    for coin, old_price in old['prices'].items():
        if old_price <= 0:
            continue
        cur = coin_price(data, coin)
        pct = ((cur - old_price) / old_price) * 100
        if pct <= -33 and announced.get(coin, 0) < now - 86400:
            drops.append((coin, old_price, cur, pct))
            announced[coin] = now
    data['drop_announced'] = announced
    return drops


# ---------------------------------------------------------------------------
# News system
# ---------------------------------------------------------------------------

def _news_context(data: dict) -> dict:
    all_nicks = [n for n in data['lines'] if n not in RESERVED]
    coins_by_price = sorted(all_nicks, key=lambda n: coin_price(data, n), reverse=True)
    coins_by_supply = sorted(all_nicks, key=lambda n: total_minted(data, n), reverse=True)

    richest = []
    players = set(list(data['usd'].keys()) + list(data['wallets'].keys()))
    for p in players:
        total = data['usd'].get(p, STARTING_USD)
        for coin, amt in data['wallets'].get(p, {}).items():
            total += amt * coin_price(data, coin)
        richest.append((p, total))
    richest.sort(key=lambda x: x[1], reverse=True)

    holder_counts: dict[str, int] = {}
    for wallet in data['wallets'].values():
        for coin, amt in wallet.items():
            if amt > 0:
                holder_counts[coin] = holder_counts.get(coin, 0) + 1
    popular = sorted(holder_counts.items(), key=lambda x: x[1], reverse=True)

    return {
        'top_coins': coins_by_price[:15],
        'top_supply': coins_by_supply[:10],
        'richest': richest[:15],
        'popular': popular[:10],
        'all': all_nicks,
    }


def _pick(lst, exclude=None):
    if not lst:
        return 'someone'
    pool = [x for x in lst if x != exclude] if exclude else lst
    return random.choice(pool or lst)


def generate_headline(data: dict, ctx: dict) -> str:
    tc = ctx['top_coins']
    rich = ctx['richest']
    pop = ctx['popular']
    sup = ctx['top_supply']
    allc = ctx['all']

    c1 = _pick(tc)
    c2 = _pick(tc, c1)
    r1 = _pick([n for n, _ in rich])
    r2 = _pick([n for n, _ in rich], r1)
    p1 = _pick([n for n, _ in pop])
    rando = _pick(allc)
    rando2 = _pick(allc, rando)

    p_c1 = coin_price(data, c1)
    p_c2 = coin_price(data, c2)
    s_c1 = total_minted(data, c1)
    d_c1 = mine_difficulty(data, c1)

    analysts = random.choice([
        'analysts say', 'experts warn', 'sources confirm', 'insiders claim',
        'a drunk guy at the bar says', 'my mom says', 'absolutely nobody says',
        'the voices in my head say', 'a fortune cookie predicted',
        'a ouija board confirmed', 'ChatGPT hallucinated',
    ])

    reaction = random.choice([
        'panic sells everything', 'doubles down', 'starts crying',
        'files a formal complaint', 'rage quits', 'blames the devs',
        'asks for a refund', 'starts a podcast about it', 'writes a medium article',
        'tweets about it from the toilet', 'pretends they saw it coming',
        'claims they never invested', 'starts a class action lawsuit',
        'moves to a cabin in the woods', 'switches to farming',
    ])

    pump_reason = random.choice([
        'after someone sneezed in the channel',
        'for absolutely no reason whatsoever',
        'because mercury is in retrograde',
        'after a single message was sent',
        'due to unprecedented vibes',
        'following a mass hallucination event',
        'because someone said the name three times',
        'after the CEO tweeted a single emoji',
        'because the chart looked like a rocket ship if you squint',
    ])

    dump_reason = random.choice([
        'after someone went to sleep',
        'because the wifi dropped for 3 seconds',
        'following allegations of touching grass',
        'after the lead dev was spotted outside',
        'due to rumors of a social life',
        'because someone finally read the whitepaper (there is none)',
        'after investors realized it\'s just IRC lines',
    ])

    quit_quote = random.choice(["Im out", "It was fun", "See you tomorrow", "I regret nothing", "Actually wait nvm"])
    study_link = random.choice(["extreme happiness", "hair loss", "improved typing speed", "loss of all friends", "chronic shitposting", "a false sense of superiority"])
    allin_take = random.choice(["bold move", "certified insanity", "actually kind of based", "this is not financial advice", "we cannot be held liable"])
    poll_take = random.choice(["overvalued", "undervalued", "a scam", "the future", "a cry for help"])
    supply_take = random.choice(["is this bullish?", "nobody knows what this means", "investors are confused", "the whitepaper is blank"])
    whale_react = random.choice(["panics", "celebrates", "doesnt notice", "files a restraining order"])
    strategy = random.choice(["I just buy whatever my cat walks on", "I flip a coin", "I ask my therapist", "I inverse whatever the channel says", "buy high sell low obviously", "I read the stars", "I close my eyes and click"])
    support_grp = random.choice(["meetings are Tuesdays at 8pm", "attendance is mandatory", "first rule: dont talk about the losses", "there are refreshments"])
    opinion = random.choice(["a steal", "highway robbery", "basically free money", "priced in already", "the top (again)"])
    gs_rating = random.choice(["buy", "sell", "strong avoid", "what is this", "please stop asking us"])
    gdp_of = random.choice(["a very small ant colony", "my savings account", "a lemonade stand", "whatever country anarcee is from"])
    quit_job = random.choice(["they never had one", "HR was relieved", "this is not a sustainable plan"])
    college = random.choice(["doesnt have kids", "also doesnt have a college fund", "will be sleeping on the couch tonight"])
    chart_pat = random.choice(["head and shoulders", "reverse head and shoulders", "middle finger", "question mark", "cry for help", "self-portrait", "upside-down cross", "bat signal"])
    vol_exceeds = random.choice(["the GDP of several micronations", "my will to live", "the number of times ive been disappointed", "all of last year combined", "what anyone thought was possible in IRC"])
    bid_result = random.choice(["moons", "barely moves", "actually goes down somehow"])
    copium = random.choice(["Were so early", "Generational wealth", "Few understand", "Not financial advice", "Trust the process", "WAGMI", "This is fine"])
    roadmap = random.choice(["step 1: chat more. step 2: ???. step 3: profit", "its just a screenshot of the channel", "404 not found", "its a napkin drawing", "the roadmap is the friends we made along the way"])
    listing_cta = random.choice(["buy before its too late", "or dont were not your dad", "this is absolutely financial advice (it isnt)"])
    herd_take = random.choice(["strength in numbers", "misery loves company", "the herd knows best"])
    oops = random.choice(["instant regret", "F in the chat", "the market thanks you for your sacrifice", "this is why we cant have nice things"])
    mined_react = random.choice(["the economy is saved", "nobody cared", "they immediately sold it", "chat erupted in applause (it didnt)"])
    bear_answer = random.choice(["yes", "no", "maybe", "ask me again in 5 minutes", "I dont even know what those words mean"])
    mover_rando = random.choice(["+0.00%", "nobody checked", "off the charts", "undefined"])
    farm_is = random.choice(["50 browser tabs open", "an autotyper", "their cat on the keyboard", "a very aggressive screensaver"])
    evidence = random.choice(["they both typed within the same minute", "their messages rhymed", "vibes", "trust me bro"])
    showdown = random.choice(["the ultimate showdown", "nobody wins", "the real losers are the people watching", "this has been going on for hours"])
    motiv_end = random.choice(["slightly more than $1,000", "slightly less", "exactly $1,000 (never traded)", "an incredible sense of regret"])

    # (text, affected_coin, sentiment)  sentiment: 'bull', 'bear', 'neutral'
    templates = [
        (f'BREAKING: ${c1} surges {random.randint(30, 800)}% {pump_reason}', c1, 'bull'),
        (f'${c1} crashes {random.randint(20, 90)}% {dump_reason} — {r1} {reaction}', c1, 'bear'),
        (f'{r1} dumps entire ${c1} bag worth {fmt_price(random.uniform(100, 50000))} — "{quit_quote}"', c1, 'bear'),
        (f'{r1} accused of insider trading on ${c1} — {analysts}', c1, 'bear'),
        (f'${c1} passes ${c2} in market cap — {r2} {reaction}', c1, 'bull'),
        (f'LEAKED: {r1} portfolio is {random.randint(40, 99)}% ${c1} — {analysts}', c1, 'bull'),
        (f'{r1} to {r2}: "You couldnt afford a single ${c1} with your whole portfolio"', c1, 'bull'),
        (f'Study finds holding ${c1} linked to {study_link}', c1, 'neutral'),
        (f'${c1} mining difficulty hits 1/{fmt_num(d_c1)} — miners switching to ${c2} in desperation', c1, 'bear'),
        (f'{r1} goes all-in on ${rando} — {analysts} "{allin_take}"', rando, 'bull'),
        (f'POLL: {random.randint(60, 99)}% of traders think ${c1} is {poll_take}', c1, 'neutral'),
        (f'${rando} has a total supply of {fmt_coins(total_minted(data, rando))} — {supply_take}', rando, 'neutral'),
        (f'Anonymous whale buys {fmt_price(random.uniform(200, 20000))} worth of ${c1}', c1, 'bull'),
        (f'{r1} reveals trading strategy: "{strategy}"', None, 'neutral'),
        (f'${c1} holders form support group — {support_grp}', c1, 'bear'),
        (f'OPINION: ${c1} at {fmt_price(p_c1)} is {opinion}', c1, 'neutral'),
        (f'{rando} hasnt chatted in {random.randint(2, 48)} hours — ${rando} coin in freefall', rando, 'bear'),
        (f'Goldman Sachs issues "{gs_rating}" rating on ${c1}', c1, 'bull' if gs_rating == 'buy' else 'bear'),
        (f'{r1} caught buying ${rando} at the absolute top — {r2} posts screenshot', rando, 'bear'),
        (f'DEBATE: Is ${c1} vs ${c2} the defining rivalry of our generation? {analysts}', None, 'neutral'),
        (f'{r1} net worth ({fmt_price(random.uniform(1000, 100000))}) now exceeds GDP of {gdp_of}', None, 'neutral'),
        (f'SEC subpoenas ${c1} — finds only {fmt_coins(s_c1)} coins and a lot of chat logs', c1, 'bear'),
        (f'${c1} staking rewards so good that {r1} quit their job — {quit_job}', c1, 'bull'),
        (f'"I put my kids college fund in ${c1}" — {r1}, who {college}', c1, 'bull'),
        (f'TECHNICAL ANALYSIS: ${c1} chart forming a {chart_pat} pattern', c1, 'neutral'),
        (f'${c1} trading volume exceeds {vol_exceeds}', c1, 'bull'),
        (f'{r1} and {r2} get into bidding war over ${rando} — price {bid_result}', rando, 'bull'),
        (f'EXCLUSIVE: {rando} planning to go silent for {random.randint(1, 30)} days — ${rando} holders brace', rando, 'bear'),
        (f'"{copium}" — {r1}, down {random.randint(10, 80)}% this week', None, 'bear'),
        (f'${c1} developers release roadmap: {roadmap}', c1, 'bull'),
        (f'NEW LISTING: ${rando2} now available — price {fmt_price(coin_price(data, rando2))} — {listing_cta}', rando2, 'bull'),
        (f'ALERT: {p1} most widely held coin with {holder_counts_for(data, p1)} holders — {herd_take}', p1, 'bull'),
        (f'{r1} accidentally typed ${c1} sell * instead of $bal — {oops}', c1, 'bear'),
        (f'BREAKING: Someone just mined a block of ${rando} — {mined_react}', rando, 'neutral'),
        (f'Bear market or buying opportunity? {r1} says "{bear_answer}"', None, 'neutral'),
        (f'TOP MOVERS: ${c1} (+{random.randint(5, 200)}%) ${c2} (-{random.randint(5, 60)}%) ${rando} ({mover_rando})', None, 'neutral'),
        (f'RUMOR: {r1} running a ${c1} mining farm — its just {farm_is}', c1, 'bull'),
        (f'SCANDAL: {r1} and {r2} accused of coordinated pump on ${rando} — evidence: {evidence}', rando, 'bear'),
        (f'${c1} HODLers vs ${c2} HODLers — {showdown}', None, 'neutral'),
        (f'MOTIVATIONAL: Remember, {r1} started with just $1,000 and now has... {motiv_end}', None, 'neutral'),
    ]

    return random.choice(templates)


def holder_counts_for(data: dict, coin: str) -> int:
    count = 0
    for wallet in data['wallets'].values():
        if wallet.get(coin.lower(), 0) > 0:
            count += 1
    return count


def _news_boost_pct() -> float:
    r = random.random()
    if r < 0.60:
        return random.uniform(5, 20)
    if r < 0.85:
        return random.uniform(20, 50)
    if r < 0.95:
        return random.uniform(50, 75)
    return random.uniform(75, 100)


def refresh_news(data: dict):
    now = time.time()
    stale = len(data.get('news_headlines', [])) > 5
    if not stale and now - data.get('news_last_gen', 0) < 3600:
        return
    ctx = _news_context(data)
    headlines = []
    count = random.randint(3, 5)
    for _ in range(count):
        text, coin, sentiment = generate_headline(data, ctx)
        h = {'text': text, 'ts': now, 'coin': coin, 'sentiment': sentiment}
        headlines.append(h)
        if coin and sentiment in ('bull', 'bear'):
            pct = _news_boost_pct()
            if sentiment == 'bear':
                pct = -pct
            duration = random.uniform(3600, 10800)
            data.setdefault('news_boost', {})[coin.lower()] = {
                'pct': pct, 'expires': now + duration, 'headline': text}
    data['news_headlines'] = headlines
    data['news_last_gen'] = now


def build_news(data: dict) -> list[str]:
    refresh_news(data)
    bar = f'{YEL}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{C}'
    lines = [
        bar,
        f' {B}{ORG}IRCoin{C}{B} {YEL}━━━{C} {B}{CYN}Market News{C}{B} '
        f'{GRY}({datetime.datetime.now().strftime("%b %d %H:%M")}){C}',
        bar,
    ]
    for i, h in enumerate(data.get('news_headlines', []), 1):
        s = h.get('sentiment', 'neutral')
        if s == 'bull':
            icon = f'{GRN}▲{C}'
        elif s == 'bear':
            icon = f'{RED}▼{C}'
        else:
            icon = f'{GRY}●{C}'
        color = random.choice([CYN, LGN, YEL, PNK, ORG, BLU])
        lines.append(f' {icon} {color}{B}{h["text"]}{B}{C}')
    lines.append(bar)
    lines.append(f' {GRY}News affects coin prices for 1-3 hours. Keep an eye on it.{C}')
    lines.append(bar)
    return lines


def known_coin(data: dict, nick: str) -> bool:
    nl = nick.lower()
    if nl in data['lines'] or nl in data['minted']:
        return True
    return nl in [n.lower() for n in data['names']]


# ---------------------------------------------------------------------------
# Wallets
# ---------------------------------------------------------------------------

def get_usd(data: dict, nick: str) -> float:
    key = nick.lower()
    if key not in data['usd']:
        data['usd'][key] = STARTING_USD
    return data['usd'][key]


def set_usd(data: dict, nick: str, amount: float):
    data['usd'][nick.lower()] = amount


def get_holdings(data: dict, nick: str, coin: str) -> float:
    return data['wallets'].get(nick.lower(), {}).get(coin.lower(), 0.0)


def set_holdings(data: dict, nick: str, coin: str, amount: float):
    key = nick.lower()
    if key not in data['wallets']:
        data['wallets'][key] = {}
    if amount <= 0:
        data['wallets'][key].pop(coin.lower(), None)
    else:
        data['wallets'][key][coin.lower()] = amount


def available_supply(data: dict, nick: str) -> float:
    return data['available'].get(nick.lower(), 0.0)


def total_minted(data: dict, nick: str) -> float:
    return data['minted'].get(nick.lower(), 0.0)


def display_nick(data: dict, nick: str) -> str:
    nl = nick.lower()
    for n in data.get('names', []):
        if n.lower() == nl:
            return n
    return nick


# ---------------------------------------------------------------------------
# IRC base
# ---------------------------------------------------------------------------

class BaseBot:
    def __init__(self, nick: str, user: str, real: str, channels: list[str]):
        self.nick     = nick
        self.user     = user
        self.real     = real
        self.channels = channels
        self.reader   = None
        self.writer   = None

    async def connect(self):
        import ssl as _ssl
        ctx = _ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode    = _ssl.CERT_NONE
        self.reader, self.writer = await asyncio.open_connection(SERVER, PORT, ssl=ctx)

    def raw(self, line: str):
        self.writer.write((line + '\r\n').encode('utf-8'))

    async def send(self, line: str):
        self.raw(line)
        await self.writer.drain()

    async def privmsg(self, target: str, msg: str):
        await self.send(f'PRIVMSG {target} :{msg}')

    async def handle_line(self, line: str):
        if line.startswith('PING'):
            await self.send('PONG' + line[4:])
            return

        parts = line.split(' ', 3)
        if len(parts) < 2:
            return
        cmd = parts[1]

        if cmd == '001':
            await asyncio.sleep(10)
            for ch in self.channels:
                self.raw(f'JOIN {ch}')
            await self.writer.drain()
            await asyncio.sleep(2)
            await self.send(f'NAMES {CHANNEL}')
            return

        if cmd == 'PRIVMSG' and len(parts) >= 4:
            nick    = parts[0][1:].split('!')[0]
            channel = parts[2]
            msg     = parts[3][1:] if parts[3].startswith(':') else parts[3]
            if nick.lower() != self.nick.lower():
                await self.on_privmsg(nick, channel, msg)

        if cmd == '353' and len(parts) >= 4:
            await self.on_names_reply(parts[3])
        elif cmd == '366':
            await self.on_names_end()

    async def on_privmsg(self, nick: str, channel: str, msg: str):
        pass

    async def on_names_reply(self, payload: str):
        pass

    async def on_names_end(self):
        pass

    async def run(self):
        while True:
            try:
                await self.connect()
                self.raw(f'NICK {self.nick}')
                self.raw(f'USER {self.user} 0 * :{self.real}')
                await self.writer.drain()
                buf = ''
                while True:
                    chunk = await self.reader.read(4096)
                    if not chunk:
                        break
                    buf += chunk.decode('utf-8', errors='replace')
                    while '\r\n' in buf:
                        line, buf = buf.split('\r\n', 1)
                        await self.handle_line(line)
            except (ConnectionResetError, OSError):
                pass
            if self.writer:
                self.writer.close()
            await asyncio.sleep(30)


# ---------------------------------------------------------------------------
# IRCoin bot
# ---------------------------------------------------------------------------

class IRCoinBot(BaseBot):
    def __init__(self, data: dict):
        super().__init__('ircoin', 'ircoin', 'IRC Coin Exchange', [CHANNEL])
        self.data = data
        self.last_cmd = 0.0
        self._names_buf: list[str] = []

    # --- NAMES tracking ---

    async def on_names_reply(self, payload: str):
        if ':' in payload:
            payload = payload.split(':', 1)[1]
        for n in payload.split():
            clean = n.lstrip('@+%~&')
            if clean and clean.lower() != self.nick.lower():
                self._names_buf.append(clean)

    async def on_names_end(self):
        if self._names_buf:
            self.data['names'] = list(set(self._names_buf))
            self._names_buf = []

    async def names_loop(self):
        while True:
            await asyncio.sleep(NAMES_INTERVAL)
            if self.writer:
                self._names_buf = []
                try:
                    await self.send(f'NAMES {CHANNEL}')
                except OSError:
                    pass

    async def staking_loop(self):
        while True:
            await asyncio.sleep(300)
            if not self.writer:
                continue
            now = time.time()
            if now - self.data.get('staking_last_calc', 0) < 3600:
                continue
            try:
                recalc_staking(self.data)
                snapshot_prices(self.data)
                drops = check_price_drops(self.data)
                for coin, old_p, new_p, pct in drops:
                    dn = display_nick(self.data, coin)
                    await self.privmsg(CHANNEL,
                        f'{RED}📉 CRASH{C} {B}{ORG}${dn}{C}{B} '
                        f'dropped {RED}{pct:.0f}%{C} in 24h! '
                        f'{GRY}({fmt_price(old_p)} → {fmt_price(new_p)}){C}')
            except OSError:
                pass

    # --- Message handling ---

    async def on_privmsg(self, nick: str, channel: str, msg: str):
        if channel.lower() != CHANNEL.lower():
            return
        if 'pizza' in nick.lower():
            return

        nl = nick.lower()

        # Hourly line tracking
        if nl not in self.data['lines']:
            self.data['lines'][nl] = {}
        hk = hour_key()
        self.data['lines'][nl][hk] = self.data['lines'][nl].get(hk, 0) + 1

        # Mining roll — chatting mines YOUR coin
        diff = mine_difficulty(self.data, nick)
        if random.randint(1, diff) == 1:
            reward = mine_reward(self.data, nick)
            miner_cut = round(reward * MINER_CUT, 4)
            pool_cut  = reward - miner_cut
            old_minted = self.data['minted'].get(nl, 0.0)
            self.data['minted'][nl] = old_minted + reward
            self.data['available'][nl] = self.data['available'].get(nl, 0.0) + pool_cut

            cur_held = get_holdings(self.data, nick, nl)
            set_holdings(self.data, nick, nl, cur_held + miner_cut)

            new_minted = self.data['minted'][nl]
            dn = display_nick(self.data, nick)
            milestone = None
            if old_minted == 0:
                milestone = f'{B}{nick}{B}\'s coin {ORG}${dn}{C} just launched! First block mined'
            else:
                for m in (100, 500, 1000, 5000, 10000, 50000, 100000):
                    if old_minted < m <= new_minted:
                        milestone = f'{ORG}${dn}{C} just crossed {B}{fmt_num(m)}{B} total supply'
                        break
            if milestone:
                try:
                    await self.privmsg(channel,
                        f'{YEL}━━━{C} {B}{GRN}⛏ MILESTONE{C}{B} {YEL}━━━{C} '
                        f'{milestone} '
                        f'{GRY}(diff: 1/{fmt_num(diff)}){C}')
                except OSError:
                    pass

        # Command routing
        stripped = msg.strip()
        if not stripped.startswith('$'):
            return

        now = time.time()
        if now - self.last_cmd < 1.0:
            return
        self.last_cmd = now

        rest = stripped[1:]
        parts = rest.split()
        if not parts:
            return

        cmd = parts[0].lower()

        if cmd == 'buy':
            pct = random.uniform(1, 3)
            wallet = self.data['wallets'].get(nl, {})
            top_coin = None
            top_val = 0.0
            for coin, amt in wallet.items():
                val = amt * coin_price(self.data, coin)
                if val > top_val:
                    top_val = val
                    top_coin = coin
            if top_coin and top_val > 0:
                amt = get_holdings(self.data, nick, top_coin)
                loss = round(amt * pct / 100, 4)
                set_holdings(self.data, nick, top_coin, amt - loss)
                dn = display_nick(self.data, top_coin)
                await self.privmsg(channel,
                    f'{RED}⚠ INVALID COMMAND{C} — lost {RED}{fmt_coins(loss)} ${dn}{C} ({pct:.1f}%) '
                    f'{GRY}(syntax: $<coin> buy <amount>){C}')
            else:
                usd = get_usd(self.data, nick)
                loss = round(usd * pct / 100, 2)
                set_usd(self.data, nick, usd - loss)
                await self.privmsg(channel,
                    f'{RED}⚠ INVALID COMMAND{C} — lost {RED}{fmt_price(loss)}{C} ({pct:.1f}%) '
                    f'{GRY}(syntax: $<coin> buy <amount>){C}')
            return

        if cmd == 'help':
            for ln in self.build_help():
                await self.privmsg(channel, ln)
            return
        if cmd in ('bal', 'portfolio'):
            for ln in self.build_portfolio(nick):
                await self.privmsg(channel, ln)
            return
        if cmd == 'top':
            for ln in self.build_top():
                await self.privmsg(channel, ln)
            return
        if cmd == 'rich':
            for ln in self.build_rich():
                await self.privmsg(channel, ln)
            return
        if cmd == 'market':
            for ln in self.build_market():
                await self.privmsg(channel, ln)
            return
        if cmd in ('staking', 'stake'):
            for ln in self.build_staking():
                await self.privmsg(channel, ln)
            return
        if cmd == 'news':
            for ln in build_news(self.data):
                await self.privmsg(channel, ln)
            return
        if cmd == 'give':
            await self.cmd_give(nick, channel, parts)
            return

        # Nick-coin commands: $<nick> [buy|sell <usd>]
        coin_nick = cmd
        if coin_nick == self.nick.lower():
            return
        if coin_nick in RESERVED:
            return
        if not known_coin(self.data, coin_nick):
            return

        if len(parts) == 1:
            for ln in self.coin_info(coin_nick):
                await self.privmsg(channel, ln)
            return

        sub = parts[1].lower()
        if sub == 'buy' and len(parts) == 3:
            await self.cmd_buy(nick, channel, coin_nick, parts[2])
        elif sub == 'sell' and len(parts) == 3:
            await self.cmd_sell(nick, channel, coin_nick, parts[2])

    # --- Coin info ---

    def coin_info(self, coin_nick: str) -> list[str]:
        cn = coin_nick.lower()
        price   = coin_price(self.data, cn)
        minted  = total_minted(self.data, cn)
        avail   = available_supply(self.data, cn)
        lines   = total_lines(self.data, cn)
        diff    = mine_difficulty(self.data, cn)
        mcap    = minted * price
        dn      = display_nick(self.data, cn)

        hours = self.data['lines'].get(cn, {})
        now   = time.time()
        d1    = now - 86400
        lines_24h = sum(v for k, v in hours.items() if int(k) >= d1)
        if lines > 0 and lines_24h > 0:
            ratio = lines_24h / max(lines - lines_24h, 1)
            chg = f'{GRN}▲ active{C}' if ratio > 0.1 else f'{RED}▼ quiet{C}'
        else:
            chg = f'{GRY}--{C}'

        out = [
            f'{B}{ORG}${dn}{C}{B} {YEL}{fmt_price(price)}{C} ({chg}) '
            f'{GRY}│{C} {CYN}Supply:{C} {B}{fmt_coins(minted)}{B} '
            f'{GRY}│{C} {CYN}Avail:{C} {B}{LGN}{fmt_coins(avail)}{B}{C} '
            f'{GRY}│{C} {CYN}Lines:{C} {B}{fmt_num(lines)}{B} '
            f'{GRY}│{C} {CYN}MCap:{C} {YEL}{fmt_price(mcap)}{C} '
            f'{GRY}│{C} {CYN}Diff:{C} {RED}1/{fmt_num(diff)}{C}'
        ]

        boost = self.data.get('news_boost', {}).get(cn, {})
        if boost and boost.get('expires', 0) > now:
            pct = boost['pct']
            headline = boost.get('headline', '')
            remaining = int((boost['expires'] - now) / 60)
            if pct > 0:
                tag = f'{GRN}▲ +{pct:.0f}%{C}'
            else:
                tag = f'{RED}▼ {pct:.0f}%{C}'
            out.append(f' {tag} {GRY}({remaining}m left){C} {headline}')

        return out

    # --- Buy / Sell ---

    async def cmd_buy(self, nick: str, channel: str, coin_nick: str, amount_str: str):
        try:
            usd = float(amount_str)
        except ValueError:
            await self.privmsg(channel, f'{RED}✗{C} Invalid amount.')
            return
        if usd <= 0:
            await self.privmsg(channel, f'{RED}✗{C} Nice try.')
            return

        bal = get_usd(self.data, nick)
        if usd > bal:
            await self.privmsg(channel,
                f'{RED}✗{C} {B}{nick}{B}: Insufficient USD. Balance: {YEL}{fmt_price(bal)}{C}')
            return

        cn = coin_nick.lower()
        price = coin_price(self.data, cn)
        coins_wanted = usd / price
        avail = available_supply(self.data, cn)

        if avail <= 0:
            dn = display_nick(self.data, cn)
            await self.privmsg(channel,
                f'{RED}✗{C} No {ORG}${dn}{C} available. They need to chat more to mine supply!')
            return

        if coins_wanted > avail:
            coins_wanted = avail
            usd = coins_wanted * price

        set_usd(self.data, nick, bal - usd)
        self.data['available'][cn] = avail - coins_wanted
        cur = get_holdings(self.data, nick, cn)
        set_holdings(self.data, nick, cn, cur + coins_wanted)
        apply_pressure(self.data, cn, usd, is_buy=True)

        dn = display_nick(self.data, cn)
        await self.privmsg(channel,
            f'{GRN}✓{C} {B}{nick}{B} bought {LGN}{fmt_coins(coins_wanted)}{C} {ORG}${dn}{C} '
            f'for {YEL}{fmt_price(usd)}{C} '
            f'{GRY}(avail: {fmt_coins(available_supply(self.data, cn))}){C}')

    async def cmd_sell(self, nick: str, channel: str, coin_nick: str, amount_str: str):
        cn = coin_nick.lower()
        price = coin_price(self.data, cn)
        held = get_holdings(self.data, nick, cn)

        if amount_str == '*':
            if held <= 0:
                dn = display_nick(self.data, cn)
                await self.privmsg(channel,
                    f'{RED}✗{C} {B}{nick}{B}: No {ORG}${dn}{C} to sell.')
                return
            coins_to_sell = held
            usd = coins_to_sell * price
        else:
            try:
                usd = float(amount_str)
            except ValueError:
                await self.privmsg(channel, f'{RED}✗{C} Invalid amount.')
                return
            if usd <= 0:
                await self.privmsg(channel, f'{RED}✗{C} Nice try.')
                return
            coins_to_sell = usd / price
            if coins_to_sell > held:
                dn = display_nick(self.data, cn)
                await self.privmsg(channel,
                    f'{RED}✗{C} {B}{nick}{B}: Insufficient {ORG}${dn}{C}. '
                    f'Holding: {LGN}{fmt_coins(held)}{C} ({YEL}{fmt_price(held * price)}{C})')
                return

        set_holdings(self.data, nick, cn, held - coins_to_sell)
        self.data['available'][cn] = self.data['available'].get(cn, 0.0) + coins_to_sell
        cur_usd = get_usd(self.data, nick)
        set_usd(self.data, nick, cur_usd + usd)
        apply_pressure(self.data, cn, usd, is_buy=False)

        dn = display_nick(self.data, cn)
        await self.privmsg(channel,
            f'{GRN}✓{C} {B}{nick}{B} sold {LGN}{fmt_coins(coins_to_sell)}{C} {ORG}${dn}{C} '
            f'for {YEL}{fmt_price(usd)}{C} USD')

    # --- Give ---

    async def cmd_give(self, nick: str, channel: str, parts: list[str]):
        # $give <player> <coin> <amount>
        if len(parts) != 4:
            await self.privmsg(channel,
                f'{RED}✗{C} Usage: {LGN}$give <player> <coin> <amount>{C}')
            return

        target = parts[1]
        coin = parts[2].lower().lstrip('$')
        raw_amount = parts[3]

        if target.lower() == nick.lower():
            await self.privmsg(channel, f'{RED}✗{C} You can\'t send coins to yourself.')
            return

        held = get_holdings(self.data, nick, coin)

        if raw_amount == '*':
            amount = held
            if amount <= 0:
                dn = display_nick(self.data, coin)
                await self.privmsg(channel,
                    f'{RED}✗{C} {B}{nick}{B}: No {ORG}${dn}{C} to give.')
                return
        else:
            try:
                amount = float(raw_amount)
            except ValueError:
                await self.privmsg(channel, f'{RED}✗{C} Invalid amount.')
                return
            if amount <= 0:
                await self.privmsg(channel, f'{RED}✗{C} Nice try.')
                return

        if amount > held:
            dn = display_nick(self.data, coin)
            await self.privmsg(channel,
                f'{RED}✗{C} {B}{nick}{B}: Insufficient {ORG}${dn}{C}. '
                f'Holding: {LGN}{fmt_coins(held)}{C}')
            return

        set_holdings(self.data, nick, coin, held - amount)
        recv = get_holdings(self.data, target, coin)
        set_holdings(self.data, target, coin, recv + amount)

        dn = display_nick(self.data, coin)
        price = coin_price(self.data, coin)
        await self.privmsg(channel,
            f'{GRN}✓{C} {B}{nick}{B} sent {LGN}{fmt_coins(amount)}{C} {ORG}${dn}{C} '
            f'({YEL}{fmt_price(amount * price)}{C}) to {B}{target}{B}')

    # --- Display builders ---

    def build_portfolio(self, nick: str) -> list[str]:
        usd = get_usd(self.data, nick)
        wallet = self.data['wallets'].get(nick.lower(), {})
        total = usd

        bar = f'{YEL}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{C}'
        lines = [
            bar,
            f' {B}{ORG}IRCoin{C}{B} {YEL}━━━{C} {B}{CYN}Portfolio:{C}{B} {B}{nick}{B}',
            bar,
            f' {YEL}{fmt_price(usd)}{C} {B}USD{B}',
        ]

        holdings = []
        for coin, amount in wallet.items():
            if amount <= 0:
                continue
            p = coin_price(self.data, coin)
            val = amount * p
            total += val
            holdings.append((coin, amount, val))

        holdings.sort(key=lambda x: x[2], reverse=True)

        if holdings:
            rows = []
            for coin, amount, val in holdings:
                dn = display_nick(self.data, coin)
                rows.append((fmt_coins(amount), dn, fmt_price(val)))
            pa = max(len(r[0]) for r in rows)
            pn = max(len(r[1]) for r in rows) + 1
            pv = max(len(r[2]) for r in rows)
            for sa, dn, sv in rows:
                lines.append(
                    f' {LGN}{sa.rjust(pa)}{C} {ORG}${dn.ljust(pn)}{C} '
                    f'{GRY}≈{C} {YEL}{sv.rjust(pv)}{C}')
        else:
            lines.append(f' {GRY}No coin holdings yet.{C} {LGN}$<nick> buy <usd>{C}')

        lines.append(bar)
        lines.append(f' {CYN}Total:{C} {B}{YEL}{fmt_price(total)}{C}{B}')
        lines.append(bar)
        return lines

    def build_top(self) -> list[str]:
        all_nicks = set(self.data['lines'].keys())
        coins = []
        for n in all_nicks:
            if n in RESERVED:
                continue
            p = coin_price(self.data, n)
            tl = total_lines(self.data, n)
            if tl > 0:
                coins.append((n, p, tl))

        coins.sort(key=lambda x: x[1], reverse=True)
        coins = coins[:15]

        bar = f'{YEL}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{C}'
        result = [bar, f' {B}{ORG}IRCoin{C}{B} {YEL}━━━{C} {B}{CYN}Top Coins by Price{C}{B}', bar]

        if not coins:
            result.append(f' {GRY}No coins yet — start chatting!{C}')
        else:
            medals = {1: f'{YEL}🥇', 2: f'{GRY}🥈', 3: f'{ORG}🥉'}
            pad = max(len(n) for n, _, _ in coins)
            for i, (n, p, tl) in enumerate(coins, 1):
                dn = display_nick(self.data, n)
                rank = medals.get(i, f'{GRY}{i:>2}.')
                minted = total_minted(self.data, n)
                result.append(
                    f' {rank}{C} {B}${dn.ljust(pad)}{B} '
                    f'{YEL}{fmt_price(p)}{C} '
                    f'{GRY}│{C} {CYN}Lines:{C} {fmt_num(tl)} '
                    f'{GRY}│{C} {CYN}Supply:{C} {fmt_coins(minted)}')

        result.append(bar)
        return result

    def build_rich(self) -> list[str]:
        players = set(list(self.data['usd'].keys()) + list(self.data['wallets'].keys()))
        port = []
        for p in players:
            total = self.data['usd'].get(p, STARTING_USD)
            wallet = self.data['wallets'].get(p, {})
            for coin, amount in wallet.items():
                total += amount * coin_price(self.data, coin)
            port.append((p, total))

        port.sort(key=lambda x: x[1], reverse=True)
        port = port[:10]

        bar = f'{YEL}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{C}'
        result = [bar, f' {B}{ORG}IRCoin{C}{B} {YEL}━━━{C} {B}{CYN}Richest Players{C}{B}', bar]

        if not port:
            result.append(f' {GRY}No players yet.{C}')
        else:
            medals = {1: f'{YEL}🥇', 2: f'{GRY}🥈', 3: f'{ORG}🥉'}
            pad = max(len(n) for n, _ in port)
            for i, (n, total) in enumerate(port, 1):
                rank = medals.get(i, f'{GRY}{i:>2}.')
                dn = display_nick(self.data, n)
                result.append(
                    f' {rank}{C} {B}{dn.ljust(pad)}{B} {GRY}—{C} {YEL}{fmt_price(total)}{C}')

        result.append(bar)
        return result

    def build_staking(self) -> list[str]:
        if not self.data.get('staking_coins'):
            recalc_staking(self.data)
        staking = self.data.get('staking_coins', [])
        bar = f'{YEL}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{C}'
        result = [
            bar,
            f' {B}{ORG}IRCoin{C}{B} {YEL}━━━{C} {B}{CYN}Staking Coins{C}{B} '
            f'{GRY}(top 10 most widely held){C}',
            bar,
        ]
        if not staking:
            result.append(f' {GRY}No staking coins yet — more people need to buy and hold!{C}')
        else:
            medals = {1: f'{YEL}🥇', 2: f'{GRY}🥈', 3: f'{ORG}🥉'}
            pad = max(len(e['coin']) for e in staking) + 1
            for i, e in enumerate(staking, 1):
                dn = display_nick(self.data, e['coin'])
                rank = medals.get(i, f'{GRY}{i:>2}.')
                result.append(
                    f' {rank}{C} {ORG}${dn.ljust(pad)}{C} '
                    f'{LGN}{e["rate"]:.2f}%/day{C} '
                    f'{GRY}│{C} {CYN}Holders:{C} {B}{e["holders"]}{B}')
        result.append(bar)
        result.append(f' {GRY}Coins with the most unique holders earn staking interest.{C}')
        result.append(bar)
        return result

    def build_market(self) -> list[str]:
        all_nicks = set(self.data['lines'].keys())
        coins = []
        for n in all_nicks:
            if n in RESERVED:
                continue
            p = coin_price(self.data, n)
            minted = total_minted(self.data, n)
            avail = available_supply(self.data, n)
            tl = total_lines(self.data, n)
            if tl > 0:
                coins.append((n, p, minted, avail, tl))

        coins.sort(key=lambda x: x[1] * x[2], reverse=True)
        coins = coins[:20]

        bar = f'{YEL}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{C}'
        result = [bar, f' {B}{ORG}IRCoin{C}{B} {YEL}━━━{C} {B}{CYN}Market Overview{C}{B}', bar]

        if not coins:
            result.append(f' {GRY}No active coins. People need to chat!{C}')
        else:
            rows = []
            for n, p, minted, avail, tl in coins:
                dn = display_nick(self.data, n)
                rows.append((dn, fmt_price(p), fmt_price(p * minted),
                             fmt_coins(minted), fmt_coins(avail)))
            pn = max(len(r[0]) for r in rows) + 1
            pp = max(len(r[1]) for r in rows)
            pm = max(len(r[2]) for r in rows)
            ps = max(len(r[3]) for r in rows)
            pa = max(len(r[4]) for r in rows)
            for dn, sp, sm, ss, sa in rows:
                result.append(
                    f' {ORG}${dn.ljust(pn)}{C} '
                    f'{YEL}{sp.rjust(pp)}{C} '
                    f'{GRY}│{C} {CYN}MCap:{C} {sm.rjust(pm)} '
                    f'{GRY}│{C} {CYN}Supply:{C} {ss.rjust(ps)} '
                    f'{GRY}│{C} {CYN}Avail:{C} {LGN}{sa.rjust(pa)}{C}')

        result.append(bar)
        return result

    def build_help(self) -> list[str]:
        bar = f'{YEL}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{C}'
        div = f'{GRY}───────────────────────────────────────────────────────────────────{C}'
        cmds = [
            ('$<nick>',              "Price, supply & stats for someone's coin"),
            ('$<nick> buy <usd>',    'Buy their coin with USD'),
            ('$<nick> sell <usd>',   'Sell their coin for USD'),
            ('$give <nick> <coin> #', 'Send coins to another player'),
            ('$bal',                 'Your portfolio & holdings'),
            ('$top',                 'Top 15 coins by price'),
            ('$rich',                'Top 10 richest players'),
            ('$staking',             'Coins earning staking interest'),
            ('$news',                'Market news — affects prices!'),
            ('$market',              'Full market overview'),
            ('$help',                'This help message'),
        ]
        lines = [
            bar,
            f' {B}{ORG}IRCoin{C}{B} {YEL}━━━{C} {B}{CYN}Every Nick is a Coin{C}{B}',
            bar,
            f' {CYN}Everyone in the channel is a tradeable coin.{C}',
            f' {CYN}The more someone {B}chats{B}, the higher their coin price goes.{C}',
            f' {CYN}Chatting {B}mines{B} new supply + earns {B}{YEL}USD{C}{B}{CYN} — harder difficulty = more money.{C}',
            f' {CYN}Mining adds coins to the market — you keep {B}20%{B}, rest goes to the pool.{C}',
            f' {CYN}Big buys pump the price, big sells dump it — trade like a real market.{C}',
            f' {CYN}Top 10 most widely held coins earn {B}{LGN}staking interest{C}{B}{CYN} (up to 3%/day)!{C}',
            f' {CYN}Last 30 days of activity affects price {B}3x{B} more than older data.{C}',
            f' {CYN}Everyone starts with {B}{YEL}$1,000 USD{C}{B}{CYN}. Place your bets.{C}',
            bar,
            f' {B}{YEL}{"COMMAND":<27}{GRY}│{C} {B}{YEL}DESCRIPTION{C}{B}',
            div,
        ]
        for cmd, desc in cmds:
            lines.append(f' {LGN}{cmd:<27}{C}{GRY}│{C} {LGY}{desc}{C}')
        lines.append(bar)
        return lines


# ---------------------------------------------------------------------------
# Background tasks
# ---------------------------------------------------------------------------

async def jitter_loop(data: dict):
    while True:
        await asyncio.sleep(JITTER_INTERVAL)
        tick_jitter(data)


async def save_loop(data: dict):
    while True:
        await asyncio.sleep(SYNC_INTERVAL)
        save_data(data)


async def main():
    data = load_data()
    tick_jitter(data)
    recalc_staking(data)

    bot = IRCoinBot(data)
    tasks = [
        asyncio.create_task(bot.run()),
        asyncio.create_task(bot.names_loop()),
        asyncio.create_task(bot.staking_loop()),
        asyncio.create_task(jitter_loop(data)),
        asyncio.create_task(save_loop(data)),
    ]
    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        pass
    finally:
        save_data(data)


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
