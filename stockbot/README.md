# 🐺 Wolf Grounds — Robinhood Stock Bot

A local web dashboard for your Robinhood account, part of the Wolf Grounds family.

**Current build: STEP 1 — read-only.** It shows your real portfolio, live
quotes, and day P&L. It does **not** place any trades. Trading arrives in a
later step, behind explicit confirmation, a max-order cap, and a kill switch.

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
| `GET /api/portfolio` | account summary + holdings            |
| `GET /api/quote?symbols=AAPL,MSFT` | live quotes             |
| `POST /api/order`    | disabled (403) until the trading step |

## Roadmap

1. **Read-only dashboard** ← you are here
2. **Paper trading** — simulated fills against live prices, same UI
3. **Live trading** — real orders, behind confirmation + max-order cap + kill switch
