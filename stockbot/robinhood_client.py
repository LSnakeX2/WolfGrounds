"""
Robinhood client wrapper for Wolf Grounds Alpha.

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

    # ── LIVE TRADING (Step 3) ────────────────────────────────────────────────
    # These place REAL orders with REAL money. The app layer gates them behind
    # TRADING_MODE=live, a kill switch, a max-order cap, and a confirm step.
    def place_live_order(self, symbol, side, quantity, order_type, limit_price=None):
        symbol = symbol.strip().upper()
        side = side.lower()
        quantity = float(quantity)

        if order_type == "limit":
            if not limit_price or float(limit_price) <= 0:
                raise ValueError("Limit price required for a limit order.")
            fn = rh.orders.order_buy_limit if side == "buy" else rh.orders.order_sell_limit
            result = fn(symbol, quantity, float(limit_price))
        else:
            fn = rh.orders.order_buy_market if side == "buy" else rh.orders.order_sell_market
            result = fn(symbol, quantity)

        # robin_stocks returns the order dict on success, or an error payload.
        if not result:
            raise RuntimeError("Order failed: no response from Robinhood.")
        if result.get("detail"):
            raise RuntimeError(result["detail"])
        if result.get("non_field_errors"):
            raise RuntimeError("; ".join(result["non_field_errors"]))
        return {
            "id": result.get("id"),
            "state": result.get("state"),
            "side": result.get("side", side),
            "quantity": result.get("quantity", quantity),
            "price": result.get("price"),
            "type": result.get("type", order_type),
        }

    def _symbol_for(self, instrument_url):
        """Resolve (and cache) a ticker symbol from an instrument URL."""
        cache = getattr(self, "_symbol_cache", None)
        if cache is None:
            cache = self._symbol_cache = {}
        if instrument_url not in cache:
            try:
                cache[instrument_url] = rh.stocks.get_symbol_by_url(instrument_url)
            except Exception:  # noqa: BLE001
                cache[instrument_url] = "?"
        return cache[instrument_url]

    def get_orders(self, open_only=False, limit=25):
        """Recent stock orders (or only currently open ones)."""
        raw = (
            rh.orders.get_all_open_stock_orders()
            if open_only
            else rh.orders.get_all_stock_orders()
        )
        rows = []
        for o in raw[:limit]:
            rows.append(
                {
                    "id": o.get("id"),
                    "symbol": self._symbol_for(o.get("instrument")),
                    "side": o.get("side"),
                    "type": o.get("type"),
                    "state": o.get("state"),
                    "quantity": float(o.get("quantity") or 0),
                    "filled": float(o.get("cumulative_quantity") or 0),
                    "price": float(o.get("price")) if o.get("price") else None,
                    "average_price": float(o.get("average_price"))
                    if o.get("average_price")
                    else None,
                    "created_at": o.get("created_at"),
                }
            )
        return rows

    def cancel_order(self, order_id):
        """Cancel an open stock order by id."""
        return rh.orders.cancel_stock_order(order_id)

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
