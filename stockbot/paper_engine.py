"""
Paper-trading engine for Wolf Grounds Alpha.

Simulates a brokerage account: starting cash, positions, and a trade log.
Orders fill against LIVE market prices (fetched from Robinhood) but no real
money moves and no real orders are placed. This is the safe sandbox where we
prove out the order flow before Step 3 wires up live trading.

State persists to a local JSON file (gitignored).
"""

import json
import os
from datetime import datetime, timezone


class PaperError(Exception):
    """Raised for invalid paper orders (insufficient funds/shares, etc.)."""


class PaperBroker:
    def __init__(self, state_path, starting_cash=10000.0):
        self._path = state_path
        self._starting_cash = float(starting_cash)
        self._state = self._load()

    # ── persistence ──────────────────────────────────────────────────────────
    def _load(self):
        if os.path.exists(self._path):
            with open(self._path, "r") as f:
                return json.load(f)
        return self._fresh()

    def _fresh(self):
        return {
            "cash": self._starting_cash,
            "starting_cash": self._starting_cash,
            "realized_pnl": 0.0,
            "positions": {},  # symbol -> {"quantity": float, "avg_cost": float}
            "trades": [],     # newest last
        }

    def _save(self):
        tmp = self._path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self._state, f, indent=2)
        os.replace(tmp, self._path)

    def reset(self):
        self._state = self._fresh()
        self._save()

    # ── trading ──────────────────────────────────────────────────────────────
    def place_order(self, symbol, side, quantity, order_type, fill_price):
        """Execute a simulated order at `fill_price` (already validated/derived
        from a live quote by the caller). Returns the recorded trade dict."""
        symbol = symbol.strip().upper()
        side = side.lower()
        quantity = float(quantity)
        fill_price = float(fill_price)

        if quantity <= 0:
            raise PaperError("Quantity must be greater than zero.")
        if fill_price <= 0:
            raise PaperError("No valid market price available for this symbol.")

        if side == "buy":
            self._buy(symbol, quantity, fill_price)
        elif side == "sell":
            self._sell(symbol, quantity, fill_price)
        else:
            raise PaperError(f"Unknown side '{side}'.")

        trade = {
            "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "price": fill_price,
            "type": order_type,
            "notional": round(quantity * fill_price, 2),
        }
        self._state["trades"].append(trade)
        self._save()
        return trade

    def _buy(self, symbol, qty, price):
        cost = qty * price
        if cost > self._state["cash"] + 1e-9:
            raise PaperError(
                f"Insufficient paper cash: need ${cost:,.2f}, "
                f"have ${self._state['cash']:,.2f}."
            )
        self._state["cash"] -= cost
        pos = self._state["positions"].get(symbol)
        if pos:
            total_qty = pos["quantity"] + qty
            pos["avg_cost"] = (pos["avg_cost"] * pos["quantity"] + cost) / total_qty
            pos["quantity"] = total_qty
        else:
            self._state["positions"][symbol] = {"quantity": qty, "avg_cost": price}

    def _sell(self, symbol, qty, price):
        pos = self._state["positions"].get(symbol)
        held = pos["quantity"] if pos else 0.0
        if qty > held + 1e-9:
            raise PaperError(
                f"Insufficient shares of {symbol}: trying to sell {qty}, hold {held}."
            )
        proceeds = qty * price
        self._state["cash"] += proceeds
        self._state["realized_pnl"] += (price - pos["avg_cost"]) * qty
        pos["quantity"] -= qty
        if pos["quantity"] <= 1e-9:
            del self._state["positions"][symbol]

    # ── reporting ────────────────────────────────────────────────────────────
    def state(self, price_lookup):
        """Return the paper account enriched with live prices.

        `price_lookup` is a callable: list[str] -> {symbol: price}.
        """
        symbols = list(self._state["positions"].keys())
        prices = price_lookup(symbols) if symbols else {}

        positions = []
        holdings_value = 0.0
        for symbol, pos in self._state["positions"].items():
            price = float(prices.get(symbol, 0) or 0)
            mkt_value = pos["quantity"] * price
            cost_basis = pos["quantity"] * pos["avg_cost"]
            holdings_value += mkt_value
            positions.append(
                {
                    "symbol": symbol,
                    "quantity": pos["quantity"],
                    "avg_cost": pos["avg_cost"],
                    "price": price,
                    "market_value": mkt_value,
                    "unrealized_pnl": mkt_value - cost_basis,
                    "unrealized_pct": ((price / pos["avg_cost"] - 1) * 100)
                    if pos["avg_cost"]
                    else 0.0,
                }
            )
        positions.sort(key=lambda p: p["market_value"], reverse=True)

        cash = self._state["cash"]
        total_equity = cash + holdings_value
        return {
            "cash": cash,
            "starting_cash": self._state["starting_cash"],
            "holdings_value": holdings_value,
            "total_equity": total_equity,
            "realized_pnl": self._state["realized_pnl"],
            "total_pnl": total_equity - self._state["starting_cash"],
            "total_pnl_pct": (
                (total_equity / self._state["starting_cash"] - 1) * 100
                if self._state["starting_cash"]
                else 0.0
            ),
            "positions": positions,
            "trades": self._state["trades"][-25:][::-1],  # newest first, last 25
        }
