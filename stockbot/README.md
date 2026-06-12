# 🐺 Wolf Grounds — Robinhood Stock Bot

A local web dashboard for your Robinhood account, part of the Wolf Grounds family.

**Current build: STEP 3 — full bot (read-only + paper + live trading).** It
shows your real portfolio, live quotes, and day P&L; a simulated paper-trading
panel; and **live trading with real money**, gated behind a confirmation step,
a hard max-order cap, and a kill switch.

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
├─ app.py               Flask backend + read-only API
├─ robinhood_client.py  robin_stocks wrapper (read-only methods)
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

## Roadmap

1. **Read-only dashboard** ✅
2. **Paper trading** — simulated fills against live prices ✅
3. **Live trading** — real orders, confirmation + max-order cap + kill switch ✅ ← you are here
