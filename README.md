# Options Trading Signal Bot

Tastytrade-powered options signal bot with Telegram alerts.
Runs during 09:30–10:30 ET (21:30–22:30 SGT).

## Project structure

```
options_bot/
├── config/
│   ├── settings.py       # env vars and trading window
│   ├── watchlist.py      # all tracked symbols
│   └── thresholds.py     # all entry/exit thresholds
├── data/
│   ├── tastytrade.py     # session, greeks, quotes
│   ├── market.py         # VIX, IVR, regime classification
│   └── candles.py        # 1-min OHLCV bars (NEW)
├── signals/
│   ├── indicators.py     # VWAP, EMA, RSI, MACD, ORB, RVOL
│   ├── strategy.py       # strategy selector, PoP, sizing
│   ├── filters.py        # entry gate checks
│   ├── sizing.py         # stop/target calculator
│   └── chain.py          # option chain strike selector (NEW)
├── alerts/
│   ├── telegram.py       # bot sender + command handlers
│   ├── formatter.py      # message formatting
│   └── late_entry.py     # /check re-evaluation
├── storage/
│   └── journal.py        # SQLite signal log
├── main.py               # scheduler + scan loop
├── Dockerfile
├── fly.toml
├── requirements.txt
├── .env.example
└── .gitignore
```

## Setup

```bash
git clone https://github.com/YOUR_USERNAME/options-bot
cd options-bot
cp .env.example .env
# fill in .env with your credentials
pip install -r requirements.txt
python main.py
```

## Environment variables

| Variable               | Description                        |
|------------------------|------------------------------------|
| TASTYTRADE_USERNAME    | Your Tastytrade login email        |
| TASTYTRADE_PASSWORD    | Your Tastytrade password           |
| TELEGRAM_BOT_TOKEN     | From @BotFather on Telegram        |
| TELEGRAM_CHAT_ID       | Your personal Telegram chat ID     |
| ACCOUNT_SIZE           | Account size in USD (e.g. 75000)   |

## Telegram commands

| Command         | Description                              |
|-----------------|------------------------------------------|
| /check SYMBOL   | Re-evaluate latest signal for ticker     |
| /status         | Bot health + current VIX regime          |
| /help           | List all commands                        |

## Deploy to Fly.io

```bash
fly auth login
fly launch
fly secrets set \
  TASTYTRADE_USERNAME=xxx \
  TASTYTRADE_PASSWORD=xxx \
  TELEGRAM_BOT_TOKEN=xxx \
  TELEGRAM_CHAT_ID=xxx \
  ACCOUNT_SIZE=75000
fly deploy
fly logs
```

## Strategy logic summary

| IVR       | Direction | Strategy          | Structure |
|-----------|-----------|-------------------|-----------|
| > 50      | Bullish   | Bull put spread   | Credit    |
| > 50      | Bearish   | Bear call spread  | Credit    |
| > 50      | Neutral   | Iron condor       | Credit    |
| < 30      | Bullish   | Bull call spread  | Debit     |
| < 30      | Bearish   | Bear put spread   | Debit     |
| 30–50     | Any       | Credit if ratio >= 30% else skip |

## VIX regime rules

| VIX Level | Regime   | Bot behaviour                                 |
|-----------|----------|-----------------------------------------------|
| < 18      | Normal   | All signals, full size                        |
| 18–28     | Elevated | All signals, half size, PoP min 70%           |
| 28–35     | Spike    | Index only (SPX/SPY/QQQ), credit only         |
| 35–45     | Spike    | Index only, 1 contract max                   |
| > 45      | Pause    | No signals, single warning message sent       |
