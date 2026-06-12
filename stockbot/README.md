# 🐺 Wolf Grounds — Robinhood Stock Bot

A local web dashboard for your Robinhood account, part of the Wolf Grounds family.

**Current build: STEP 4 — full bot.** Read-only portfolio/quotes, a paper
sandbox, **live trading** (confirmation + max-order cap + kill switch), live
**open-orders/history with cancel**, and a background scheduler for
**automated strategies** (DCA / rebalance).

Powered by the unofficial [`robin_stocks`](https://github.com/jmfernandes/robin_stocks)
library.

---

## ⚠️ Read this before running

- **Localhost only.** The server binds to `127.0.0.1`. Anyone who can reach it
  can read your brokerage account — never expose it to the internet without
  adding real authentication.
- **Credentials are sensitive.** Your `.env` and the `robin_stocks` session
  token can move real money. They are gitignored. Never commit them.
- **Unofficial API.** `robin_stocks` is reverse-engineered. It can break or be
  rate-limited without notice, and may bump Robinhood's terms of service.

---

## Setup

```bash
cd stockbot
python -m venv .venv && source .venv/bin/activate   # optional but recommended
pip install -r requirements.txt

cp .env.example .env        # then edit .env with your real credentials
python app.py
```

Open <http://127.0.0.1:5000>.

### MFA / two-factor

Set `ROBINHOOD_TOTP_SECRET` in `.env` to the base32 secret from Robinhood's
authenticator-app setup (Settings → Security → Two-Factor → Authentication app
→ "Can't scan?"). With it set, login is automatic. Leave it blank to be
prompted for a code in the terminal instead.

---

## What's here

```
stockbot/
├─ app.py               Flask backend + API
├─ robinhood_client.py  robin_stocks wrapper
├─ paper_engine.py      paper-trading sandbox
├─ strategy_engine.py   automated-strategy scheduler
├─ notifier.py          Discord webhook push alerts
├─ discord_bridge.py    interactive Discord bot
├─ requirements.txt
├─ .env.example         copy to .env and fill in
└─ frontend/            dashboard (index.html / style.css / app.js)
```

## API (read-only)

| Endpoint         | Returns                                   |
|------------------|-------------------------------------------|
| `GET /api/status`    | login state + trading mode            |
| `GET /api/portfolio` | real account summary + holdings       |
| `GET /api/quote?symbols=AAPL,MSFT` | live quotes             |
| `GET /api/paper`     | paper account state (paper mode)      |
| `POST /api/paper/reset` | reset the paper account            |
| `POST /api/order`    | place an order (see modes below)      |
| `POST /api/kill`     | engage/disengage the live kill switch |
| `GET /api/orders[?open=1]` | live orders (history or open only) |
| `POST /api/orders/cancel`  | cancel a live order (live mode)   |
| `GET /api/strategies` | list automated strategies            |
| `POST /api/strategies` | add a strategy (starts disabled)    |
| `POST /api/strategies/<id>/toggle` | enable/disable          |
| `POST /api/strategies/<id>/run`    | run once now            |
| `DELETE /api/strategies/<id>`      | delete                  |

### Paper trading

Set `TRADING_MODE=paper` in `.env` (and optionally `PAPER_STARTING_CASH`) and
restart. The dashboard shows a Buy/Sell panel and a paper portfolio. Orders
fill at the live market price (market orders) or at your limit if it's
marketable right now (resting limit orders come later). Paper state persists to
`paper_account.json` (gitignored); "reset account" wipes it.

### ⚠️ Live trading — real money

Set `TRADING_MODE=live`. **Every order places a real trade.** Guardrails:

- **Confirmation step** — the dashboard previews each order (action, qty, est.
  cost) in a modal; nothing is sent until you click "Place real order".
- **Max-order cap** — `MAX_ORDER_USD` (and optional `MAX_ORDER_SHARES`) are
  enforced **server-side**. Orders above the cap are rejected no matter what the
  UI sends. Start small.
- **Kill switch** — one button disables all live orders instantly. Start with
  `TRADING_KILLED=true` to require an explicit arm before any trade.
- A red **LIVE TRADING** banner is always visible in live mode.

`/api/order` behaviour by mode: `read_only` → 403 · `paper` → simulated fill ·
`live` → returns a preview unless `confirm: true`, then places the real order
(subject to kill switch + caps).

**Strongly recommended:** paper-trade first, then go live with a low
`MAX_ORDER_USD` and the kill switch armed until you trust it.

### Live orders & cancel

In live mode the dashboard shows a **Live Orders** panel (recent + open) with a
**cancel** button on any still-open order.

### 🤖 Automated strategies

A background scheduler runs saved strategies on a timer. It works in **paper**
or **live** mode, and every order it generates goes through the **same kill
switch and max-order cap** as manual trades.

- **DCA / recurring** — buy or sell a fixed dollar amount (or share count) of a
  symbol on an interval (hourly / daily / weekly).
- **Rebalance** — given target weights (e.g. `AAPL:0.5, MSFT:0.5`), trade toward
  them when a position drifts past a minimum-dollar threshold.

Strategies **start disabled** — nothing trades automatically until you click
*enable*. Saved to `strategies.json` (gitignored). Test them in paper mode (or
with the kill switch armed) before letting them run live.

## 🤖 Discord bridge

Matches the other Wolf Grounds Discord bots. Two independent pieces, both
optional:

**Push alerts (no bot needed).** Set `DISCORD_WEBHOOK_URL` to a channel webhook.
The server posts a message whenever a strategy runs or a live order is
submitted.

**Interactive bot.** Run alongside the Flask app:

```bash
python app.py            # terminal 1 — the API
python discord_bridge.py # terminal 2 — the Discord bot
```

Commands (prefix `!` by default):

| Command | Does |
|---------|------|
| `!portfolio` / `!paper` | account summary + holdings |
| `!quote AAPL MSFT` | live quotes |
| `!orders` | open live orders |
| `!strategies` | list strategies |
| `!status` | mode, kill switch, cap |
| `!buy SYM QTY` / `!sell SYM QTY` | place order (live needs a ✅ reaction) |
| `!enable ID` / `!disable ID` | toggle a strategy |
| `!kill` / `!resume` | engage/disengage the kill switch |

**Security:** set `DISCORD_CHANNEL_ID` (restricts commands to one channel) and
`DISCORD_ALLOWED_USER_IDS` (only these users can trade/control). The bridge just
calls the API, so the same cap, kill switch, and live-confirmation all apply.

## Roadmap

1. **Read-only dashboard** ✅
2. **Paper trading** — simulated fills against live prices ✅
3. **Live trading** — real orders, confirmation + max-order cap + kill switch ✅
4. **Orders/cancel + automated strategies** (DCA, rebalance) ✅
5. **Discord bridge** — alerts + interactive bot ✅ ← you are here
