"""
Robinhood client wrapper for the Wolf Grounds Stock Bot.

STEP 1 (this file): READ-ONLY. It logs in and reads account/portfolio/quote
data. There are deliberately no order-placing methods here yet — live trading
gets added in a later step behind explicit guardrails.

Backed by the unofficial `robin_stocks` library. Treat the session token it
creates as a password: it can move real money.
"""

import os
import pyotp
import robin_stocks.robinhood as rh


class RobinhoodClient:
    def __init__(self, username, password, totp_secret=None):
        self._username = username
        self._password = password
        self._totp_secret = totp_secret
        self._logged_in = False

    # ── auth ───────────────────────────────────────────────────────────────
    def login(self):
        """Authenticate. Uses a TOTP secret if provided (no SMS prompt),
        otherwise robin_stocks will prompt for an MFA code on the terminal."""
        mfa_code = None
        if self._totp_secret:
            mfa_code = pyotp.TOTP(self._totp_secret).now()

        rh.login(
            username=self._username,
            password=self._password,
            mfa_code=mfa_code,
            store_session=True,
        )
        self._logged_in = True

    @property
    def logged_in(self):
        return self._logged_in

    # ── read-only data ───────────────────────────────────────────────────────
    def get_summary(self):
        """High-level account numbers: total value, cash, day change."""
        phoenix = rh.account.load_phoenix_account()
        profile = rh.profiles.load_portfolio_profile()

        def amount(node):
            # phoenix fields look like {"amount": "123.45", "currency_code": "USD"}
            if isinstance(node, dict) and "amount" in node:
                return float(node["amount"])
            return float(node) if node not in (None, "") else 0.0

        total_equity = amount(phoenix.get("total_equity"))
        cash = amount(phoenix.get("uninvested_cash"))
        market_value = amount(phoenix.get("portfolio_equity")) or amount(
            phoenix.get("market_value")
        )

        equity_now = float(profile.get("equity") or 0)
        prev_close = float(profile.get("equity_previous_close") or 0)
        day_change = equity_now - prev_close if prev_close else 0.0
        day_change_pct = (day_change / prev_close * 100) if prev_close else 0.0

        return {
            "total_equity": total_equity,
            "cash": cash,
            "market_value": market_value,
            "day_change": day_change,
            "day_change_pct": day_change_pct,
        }

    def get_holdings(self):
        """List of current stock positions with live price and P&L."""
        holdings = rh.account.build_holdings()
        rows = []
        for symbol, h in holdings.items():
            rows.append(
                {
                    "symbol": symbol,
                    "name": h.get("name", ""),
                    "quantity": float(h.get("quantity") or 0),
                    "price": float(h.get("price") or 0),
                    "average_buy_price": float(h.get("average_buy_price") or 0),
                    "equity": float(h.get("equity") or 0),
                    "percent_change": float(h.get("percent_change") or 0),
                    "equity_change": float(h.get("equity_change") or 0),
                    "portfolio_pct": float(h.get("percentage") or 0),
                }
            )
        rows.sort(key=lambda r: r["equity"], reverse=True)
        return rows

    def get_prices(self, symbols):
        """Map of {symbol: last_trade_price} for quick valuation lookups."""
        return {q["symbol"]: q["price"] for q in self.get_quotes(symbols)}

    def get_quotes(self, symbols):
        """Live quote lookup for one or more ticker symbols."""
        symbols = [s.strip().upper() for s in symbols if s.strip()]
        if not symbols:
            return []
        quotes = rh.stocks.get_quotes(symbols)
        out = []
        for q in quotes:
            if not q:
                continue
            last = float(q.get("last_trade_price") or 0)
            prev = float(q.get("previous_close") or 0)
            change = last - prev if prev else 0.0
            out.append(
                {
                    "symbol": q.get("symbol", ""),
                    "price": last,
                    "previous_close": prev,
                    "change": change,
                    "change_pct": (change / prev * 100) if prev else 0.0,
                    "ask": float(q.get("ask_price") or 0),
                    "bid": float(q.get("bid_price") or 0),
                }
            )
        return out
