"""
Automated strategy engine for Wolf Grounds Alpha.

Runs scheduled strategies in a background thread:
  - "dca":       recurring buy/sell of a fixed dollar amount or share count.
  - "rebalance": move holdings toward target weights when they drift past a
                 threshold.

Every order a strategy generates is sent through the SAME executor the
dashboard uses, so the kill switch and max-order cap always apply. Strategies
are disabled by default and persist to a gitignored JSON file.

The engine is deliberately dependency-injected (callables passed in) so it
holds no Robinhood logic itself and is easy to test.
"""

import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone


def _now():
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.isoformat(timespec="seconds")


class StrategyEngine:
    def __init__(
        self,
        state_path,
        execute_order,      # (symbol, side, qty, type, limit_price, last_price) -> dict; raises on reject
        get_prices,         # (list[str]) -> {symbol: price}
        get_portfolio,      # () -> (total_equity, {symbol: market_value})
        can_trade,          # () -> bool  (mode is paper/live AND not killed)
        on_event=None,      # (str) -> None  optional; called with a run summary
        check_interval=30,
    ):
        self._path = state_path
        self._execute = execute_order
        self._get_prices = get_prices
        self._get_portfolio = get_portfolio
        self._can_trade = can_trade
        self._on_event = on_event or (lambda text: None)
        self._check_interval = check_interval
        self._lock = threading.Lock()
        self._strategies = self._load()
        self._thread = None
        self._stop = threading.Event()

    # ── persistence ──────────────────────────────────────────────────────────
    def _load(self):
        if os.path.exists(self._path):
            with open(self._path, "r") as f:
                return json.load(f)
        return []

    def _save(self):
        tmp = self._path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self._strategies, f, indent=2)
        os.replace(tmp, self._path)

    # ── CRUD ─────────────────────────────────────────────────────────────────
    def list(self):
        with self._lock:
            return [dict(s) for s in self._strategies]

    def add(self, spec):
        s = self._normalize(spec)
        with self._lock:
            self._strategies.append(s)
            self._save()
        return s

    def _normalize(self, spec):
        stype = (spec.get("type") or "").lower()
        base = {
            "id": uuid.uuid4().hex[:8],
            "type": stype,
            "enabled": False,  # opt-in: never auto-trade until explicitly enabled
            "interval_seconds": int(spec.get("interval_seconds") or 86400),
            "created_at": _iso(_now()),
            "last_run": None,
            "last_result": None,
        }
        if stype == "dca":
            base.update(
                {
                    "symbol": (spec.get("symbol") or "").strip().upper(),
                    "side": (spec.get("side") or "buy").lower(),
                    "amount_usd": float(spec.get("amount_usd") or 0),
                    "shares": float(spec.get("shares") or 0),
                }
            )
            if not base["symbol"]:
                raise ValueError("DCA strategy needs a symbol.")
            if base["amount_usd"] <= 0 and base["shares"] <= 0:
                raise ValueError("DCA strategy needs amount_usd or shares.")
        elif stype == "rebalance":
            targets = {
                k.strip().upper(): float(v) for k, v in (spec.get("targets") or {}).items()
            }
            total = sum(targets.values())
            if not targets or abs(total - 1.0) > 0.01:
                raise ValueError("Rebalance targets must be weights summing to ~1.0.")
            base.update(
                {
                    "targets": targets,
                    "threshold_usd": float(spec.get("threshold_usd") or 25),
                }
            )
        else:
            raise ValueError(f"Unknown strategy type '{stype}'.")
        return base

    def set_enabled(self, sid, enabled):
        with self._lock:
            for s in self._strategies:
                if s["id"] == sid:
                    s["enabled"] = bool(enabled)
                    self._save()
                    return dict(s)
        raise KeyError(sid)

    def delete(self, sid):
        with self._lock:
            self._strategies = [s for s in self._strategies if s["id"] != sid]
            self._save()

    def run_now(self, sid):
        with self._lock:
            target = next((s for s in self._strategies if s["id"] == sid), None)
        if not target:
            raise KeyError(sid)
        self._run_one(target, force=True)
        return self.get(sid)

    def get(self, sid):
        with self._lock:
            for s in self._strategies:
                if s["id"] == sid:
                    return dict(s)
        return None

    # ── scheduler ────────────────────────────────────────────────────────────
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as e:  # noqa: BLE001
                print(f"[strategy] tick error: {e}")
            self._stop.wait(self._check_interval)

    def _tick(self):
        if not self._can_trade():
            return
        with self._lock:
            due = [s for s in self._strategies if self._is_due(s)]
        for s in due:
            self._run_one(s)

    def _is_due(self, s):
        if not s.get("enabled"):
            return False
        if not s.get("last_run"):
            return True
        last = datetime.fromisoformat(s["last_run"])
        return (_now() - last).total_seconds() >= s["interval_seconds"]

    # ── execution ────────────────────────────────────────────────────────────
    def _run_one(self, s, force=False):
        if not force and not self._can_trade():
            return
        try:
            if s["type"] == "dca":
                msg = self._run_dca(s)
            elif s["type"] == "rebalance":
                msg = self._run_rebalance(s)
            else:
                msg = f"unknown type {s['type']}"
            result = {"time": _iso(_now()), "ok": True, "detail": msg}
        except Exception as e:  # noqa: BLE001
            result = {"time": _iso(_now()), "ok": False, "detail": str(e)}

        with self._lock:
            for st in self._strategies:
                if st["id"] == s["id"]:
                    st["last_run"] = _iso(_now())
                    st["last_result"] = result
                    self._save()
                    break

        icon = "✅" if result["ok"] else "⚠️"
        self._on_event(f"{icon} [strategy {s['type']}] {result['detail']}")

    def _price(self, symbol):
        p = self._get_prices([symbol]).get(symbol, 0)
        if not p:
            raise RuntimeError(f"no price for {symbol}")
        return float(p)

    def _run_dca(self, s):
        symbol = s["symbol"]
        price = self._price(symbol)
        qty = s["shares"] if s["shares"] > 0 else round(s["amount_usd"] / price, 6)
        if qty <= 0:
            raise RuntimeError("computed zero quantity")
        self._execute(symbol, s["side"], qty, "market", None, price)
        return f"{s['side']} {qty} {symbol} @ ~${price:,.2f}"

    def _run_rebalance(self, s):
        targets = s["targets"]
        total, values = self._get_portfolio()
        if total <= 0:
            raise RuntimeError("portfolio value is zero")
        prices = self._get_prices(list(targets))
        actions = []
        for symbol, weight in targets.items():
            price = float(prices.get(symbol, 0) or 0)
            if price <= 0:
                actions.append(f"{symbol}: no price, skipped")
                continue
            target_val = weight * total
            current_val = float(values.get(symbol, 0) or 0)
            delta = target_val - current_val
            if abs(delta) < s["threshold_usd"]:
                continue
            side = "buy" if delta > 0 else "sell"
            qty = round(abs(delta) / price, 6)
            try:
                self._execute(symbol, side, qty, "market", None, price)
                actions.append(f"{side} {qty} {symbol}")
            except Exception as e:  # noqa: BLE001
                actions.append(f"{symbol}: {e}")
        return "; ".join(actions) if actions else "already balanced"
