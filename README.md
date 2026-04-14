# IRCoin

IRC cryptocurrency simulation where every nick in the channel is a tradeable coin, valued by chat activity.

## How It Works

Every person in the channel is a coin. The more they chat, the higher their coin's price goes. Chatting also mines new supply — 20% goes to the miner, 80% goes to the market pool for others to buy. Mining difficulty scales up as more coins are minted, and a nick needs at least 25 lines before their coin exists.

Players start with $1,000 USD and can buy/sell any coin. Large trades move prices via supply/demand pressure, just like a real market. The last 30 days of chat activity influences price 3x more than older data.

### Staking
The top 10 most widely held coins earn staking interest for holders — #1 gets 3%/day, scaling down to 0.5%/day for #10. Interest accrues hourly.

### News
A dynamic news system generates 3-5 headlines every hour with bull/bear/neutral sentiment. News events temporarily affect coin prices by 5-100% for 1-3 hours.

### Exchange Modes
- **Maintenance** — Randomly once per day, the exchange goes down for 1 hour. No trading allowed, but `$give` still works.
- **Fee Mode** — Activates 1-2x per day for 1 hour. A 1-5% fee on buys, sells, or both gets sent to the coin minter's wallet.

### Rug Pulls
If a nick hasn't chatted for 21 days, their coin gets delisted entirely. All holder losses over $100 are announced.

### Blockchain
Every trade is logged to an append-only SQLite ledger with chained SHA-256 hashes. Query it with `$blockchain`.

## Commands

| Command | Description |
|---|---|
| `$<nick>` | Price, supply, difficulty & stats for a coin |
| `$<nick> buy <usd>` | Buy a coin with USD |
| `$<nick> sell <usd>` | Sell a coin for USD (use `*` to sell all) |
| `$give <player> <coin> <amount>` | Transfer coins to another player (use `*` for all) |
| `$bal` | Your portfolio & holdings |
| `$top` | Top 15 coins by price |
| `$rich` | Top 10 richest players |
| `$market` | Full market overview + active mode status |
| `$staking` | Coins earning staking interest |
| `$news` | Market news + active price effects |
| `$blockchain` | Last 20 trades globally |
| `$blockchain <nick>` | Last 20 trades by a player |
| `$blockchain $<coin>` | Last 20 trades of a coin |
| `$help` | Help message |

## Data

| File | Purpose |
|---|---|
| `ircoin_data.json` | Game state (wallets, lines, supply, etc.) — saved every 5 minutes |
| `ircoin_ledger.db` | Append-only trade ledger (SQLite) |
| `backups/` | Daily JSON snapshots, last 7 days retained |

## Running

```
python ircoin.py
```

No dependencies beyond the Python standard library.
