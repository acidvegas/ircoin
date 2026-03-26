# IRCoin

IRC cryptocurrency simulation where every nick in the channel is a tradeable coin, valued by chat activity.

## How It Works

Every person in the channel is a coin. The more they chat, the higher their coin's price goes. Chatting also mines new supply — 20% goes to the miner, 80% goes to the market pool for others to buy. Mining difficulty increases as more coins are minted.

Players start with $1,000 USD and can buy/sell any coin. Large trades move prices like a real market. The last 30 days of chat activity influences price 3x more than older data.

The top 10 most widely held coins earn staking interest for holders (up to 3%/day). A dynamic news system generates headlines every hour that temporarily affect coin prices.

## Commands

| Command | Description |
|---|---|
| `$<nick>` | Price, supply, difficulty & stats for a coin |
| `$<nick> buy <usd>` | Buy a coin with USD |
| `$<nick> sell <usd>` | Sell a coin for USD (use `*` to sell all) |
| `$give <player> <coin> <amount>` | Transfer coins to another player (use `*` for all) |
| `$bal` | View your portfolio & holdings |
| `$top` | Top 15 coins by price |
| `$rich` | Top 10 richest players |
| `$market` | Full market overview |
| `$staking` | Coins earning staking interest |
| `$news` | Market news (affects prices for 1-3 hours) |
| `$help` | Help message |

## Running

```
python ircoin.py
```

No dependencies beyond the Python standard library. State is persisted to `ircoin_data.json` every 5 minutes.
