"""
Wolf Grounds Alpha — Flask backend.

A local web dashboard for Robinhood: read-only portfolio/quotes, a paper-trading
sandbox, live trading (behind confirmation + max-order cap + kill switch), live
open-orders/history with cancel, and a background scheduler for automated
strategies (DCA, rebalance). Mode is selected by TRADING_MODE in .env.

SAFETY: run this on localhost only. Anyone who can reach this server can read
your account and — in live mode — trade it. Never expose it to the public
internet without real auth.
"""

import os
import secrets
import threading
from flask import Flask, Response, jsonify, request, send_from_directory
from dotenv import load_dotenv

from robinhood_client import RobinhoodClient
from paper_engine import PaperBroker, PaperError
from strategy_engine import StrategyEngine
from notifier import make_notifier

load_dotenv()

HERE = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.join(HERE, "frontend")

app = Flask(__name__, static_folder=None)

# ── Dashboard auth ────────────────────────────────────────────────────────────
# Set DASHBOARD_PASSWORD in .env to enable HTTP Basic Auth. Required whenever
# HOST=0.0.0.0 (cloud/public deployment). Leave blank for localhost-only use.
_DASH_USER = os.getenv("DASHBOARD_USERNAME", "admin")
_DASH_PASS = os.getenv("DASHBOARD_PASSWORD", "")


@app.before_request
def _enforce_auth():
    if not _DASH_PASS:
        return  # no password configured → localhost-only, no challenge
    creds = request.authorization
    ok = (
        creds is not None
        and secrets.compare_digest(creds.username, _DASH_USER)
        and secrets.compare_digest(creds.password, _DASH_PASS)
    )
    if not ok:
        return Response(
            "Authentication required.",
            401,
            {"WWW-Authenticate": 'Basic realm="Wolf Grounds Alpha"'},
        )


# Single shared client for this local single-user dashboard.
client = None
login_error = None
TRADING_MODE = os.getenv("TRADING_MODE", "read_only").lower()

# Paper-trading sandbox (Step 2). Active whenever TRADING_MODE == "paper".
paper = PaperBroker(
    state_path=os.path.join(HERE, "paper_account.json"),
    starting_cash=float(os.getenv("PAPER_STARTING_CASH", "10000")),
)

# ── Live-trading guardrails (Step 3) ─────────────────────────────────────────
# Hard cap on the dollar value of any single order. A live order above this is
# rejected by the server, no matter what the UI sends.
MAX_ORDER_USD = float(os.getenv("MAX_ORDER_USD", "500"))
# Optional cap on share count per order (0 = no share cap).
MAX_ORDER_SHARES = float(os.getenv("MAX_ORDER_SHARES", "0"))
# Kill switch: when engaged, all live orders are refused. Starts engaged if
# TRADING_KILLED=true in .env; can be toggled at runtime via /api/kill.
trading_killed = os.getenv("TRADING_KILLED", "false").lower() in ("1", "true", "yes")

# Serializes order execution so the dashboard and the strategy scheduler can't
# place orders concurrently (and so paper-state writes don't race).
exec_lock = threading.Lock()


def execute_order(symbol, side, quantity, order_type, limit_price, last_price):
    """Single execution core for ALL order flow (dashboard + strategies).

    Enforces mode, kill switch, and caps. Returns a result dict; raises
    PaperError / RuntimeError / ValueError on rejection.
    """
    with exec_lock:
        if TRADING_MODE == "paper":
            if order_type == "limit":
                marketable = (
                    last_price <= limit_price if side == "buy" else last_price >= limit_price
                )
                if not marketable:
                    raise PaperError(
                        f"Limit not marketable: {symbol} at ${last_price:,.2f}, "
                        f"limit ${limit_price:,.2f}. (Resting orders come later.)"
                    )
                fill_price = limit_price
            else:
                fill_price = last_price
            trade = paper.place_order(symbol, side, quantity, order_type, fill_price)
            return {"mode": "paper", "trade": trade}

        # LIVE — real money.
        if trading_killed:
            raise RuntimeError("Kill switch engaged — live trading is disabled.")
        notional = quantity * (limit_price if order_type == "limit" else last_price)
        if notional > MAX_ORDER_USD:
            raise RuntimeError(
                f"Order ${notional:,.2f} exceeds the max-order cap of ${MAX_ORDER_USD:,.2f}."
            )
        if MAX_ORDER_SHARES and quantity > MAX_ORDER_SHARES:
            raise RuntimeError(f"Quantity {quantity} exceeds the {MAX_ORDER_SHARES}-share cap.")
        order = client.place_live_order(symbol, side, quantity, order_type, limit_price)
        return {"mode": "live", "order": order}


def portfolio_snapshot():
    """(total_equity, {symbol: market_value}) for the active mode — used by
    rebalance strategies."""
    if TRADING_MODE == "paper":
        st = paper.state(client.get_prices)
        return st["total_equity"], {p["symbol"]: p["market_value"] for p in st["positions"]}
    summary = client.get_summary()
    holdings = client.get_holdings()
    return summary["total_equity"], {h["symbol"]: h["equity"] for h in holdings}


def can_trade():
    return TRADING_MODE in ("paper", "live") and not trading_killed and client is not None


# Optional Discord push notifications (webhook). No-op if unset.
notify = make_notifier(os.getenv("DISCORD_WEBHOOK_URL"))

# Automated-strategy scheduler (Step 4). Thread starts in __main__.
strategies = StrategyEngine(
    state_path=os.path.join(HERE, "strategies.json"),
    execute_order=execute_order,
    get_prices=lambda syms: client.get_prices(syms),
    get_portfolio=portfolio_snapshot,
    can_trade=can_trade,
    on_event=lambda text: notify(text),
)


def init_client():
    """Log in once at startup using credentials from .env."""
    global client, login_error
    username = os.getenv("ROBINHOOD_USERNAME")
    password = os.getenv("ROBINHOOD_PASSWORD")
    totp = os.getenv("ROBINHOOD_TOTP_SECRET") or None

    if not username or not password:
        login_error = "Missing ROBINHOOD_USERNAME / ROBINHOOD_PASSWORD in .env"
        return

    try:
        c = RobinhoodClient(username, password, totp)
        c.login()
        client = c
        login_error = None
        print("✓ Logged in to Robinhood.")
    except Exception as e:  # noqa: BLE001
        login_error = f"Login failed: {e}"
        print(f"✗ {login_error}")


def require_client():
    if client is None or not client.logged_in:
        return jsonify({"error": login_error or "Not logged in"}), 503
    return None


# ── frontend ────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.route("/<path:path>")
def static_files(path):
    return send_from_directory(FRONTEND_DIR, path)


# ── api (read-only) ──────────────────────────────────────────────────────────
@app.route("/api/status")
def api_status():
    return jsonify(
        {
            "logged_in": bool(client and client.logged_in),
            "trading_mode": TRADING_MODE,
            "error": login_error,
            "killed": trading_killed,
            "max_order_usd": MAX_ORDER_USD,
            "max_order_shares": MAX_ORDER_SHARES,
        }
    )


@app.route("/api/kill", methods=["POST"])
def api_kill():
    """Engage/disengage the live-trading kill switch."""
    global trading_killed
    data = request.get_json(silent=True) or {}
    trading_killed = bool(data.get("enabled", True))
    return jsonify({"killed": trading_killed})


@app.route("/api/portfolio")
def api_portfolio():
    guard = require_client()
    if guard:
        return guard
    try:
        return jsonify(
            {"summary": client.get_summary(), "holdings": client.get_holdings()}
        )
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e)}), 500


@app.route("/api/quote")
def api_quote():
    guard = require_client()
    if guard:
        return guard
    symbols = request.args.get("symbols", "")
    try:
        return jsonify({"quotes": client.get_quotes(symbols.split(","))})
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e)}), 500


@app.route("/api/paper")
def api_paper():
    """Current paper account state, enriched with live prices."""
    guard = require_client()
    if guard:
        return guard
    try:
        return jsonify(paper.state(client.get_prices))
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e)}), 500


@app.route("/api/paper/reset", methods=["POST"])
def api_paper_reset():
    paper.reset()
    return jsonify({"ok": True})


@app.route("/api/order", methods=["POST"])
def api_order():
    """Place an order, routed by TRADING_MODE.

      read_only -> 403 (no trading)
      paper     -> simulated fill against the live market price
      live      -> REAL order. Requires confirm=true; without it, returns a
                   preview. Gated by the kill switch and the max-order cap.
    """
    if TRADING_MODE == "read_only":
        return jsonify({"error": "Trading is disabled (TRADING_MODE=read_only)."}), 403
    if TRADING_MODE not in ("paper", "live"):
        return jsonify({"error": f"Unknown TRADING_MODE '{TRADING_MODE}'."}), 400

    guard = require_client()
    if guard:
        return guard

    data = request.get_json(silent=True) or {}
    symbol = (data.get("symbol") or "").strip().upper()
    side = (data.get("side") or "").strip().lower()
    order_type = (data.get("type") or "market").strip().lower()
    try:
        quantity = float(data.get("quantity") or 0)
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid quantity."}), 400

    if not symbol or side not in ("buy", "sell") or quantity <= 0:
        return jsonify({"error": "symbol, side (buy/sell), and quantity are required."}), 400

    limit_price = None
    if order_type == "limit":
        try:
            limit_price = float(data.get("limit_price") or 0)
        except (TypeError, ValueError):
            return jsonify({"error": "Invalid limit price."}), 400
        if limit_price <= 0:
            return jsonify({"error": "Limit price required for limit orders."}), 400

    # Live market data for pricing/preview.
    quotes = client.get_quotes([symbol])
    if not quotes:
        return jsonify({"error": f"No market data for {symbol}."}), 400
    last = quotes[0]["price"]
    est_price = limit_price if order_type == "limit" else last
    est_notional = quantity * est_price

    if TRADING_MODE == "paper":
        try:
            result = execute_order(symbol, side, quantity, order_type, limit_price, last)
        except PaperError as e:
            return jsonify({"error": str(e)}), 422
        return jsonify(
            {"ok": True, "trade": result["trade"], "paper": paper.state(client.get_prices)}
        )

    # ── LIVE ────────────────────────────────────────────────────────────────
    if trading_killed:
        return jsonify({"error": "Kill switch engaged — live trading is disabled."}), 423

    # Surface cap breaches early (also re-checked inside execute_order).
    if est_notional > MAX_ORDER_USD:
        return (
            jsonify(
                {
                    "error": f"Order ${est_notional:,.2f} exceeds the max-order cap "
                    f"of ${MAX_ORDER_USD:,.2f}. Raise MAX_ORDER_USD in .env to allow it."
                }
            ),
            422,
        )
    if MAX_ORDER_SHARES and quantity > MAX_ORDER_SHARES:
        return (
            jsonify({"error": f"Quantity {quantity} exceeds the {MAX_ORDER_SHARES}-share cap."}),
            422,
        )

    preview = {
        "symbol": symbol,
        "side": side,
        "quantity": quantity,
        "type": order_type,
        "limit_price": limit_price,
        "last_price": last,
        "est_price": est_price,
        "est_notional": round(est_notional, 2),
    }

    # Two-step: no confirm flag => return the preview, place nothing.
    if not data.get("confirm"):
        return jsonify({"preview": preview})

    try:
        result = execute_order(symbol, side, quantity, order_type, limit_price, last)
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": f"Order rejected: {e}"}), 422

    notify(f"✅ LIVE {side.upper()} {quantity} {symbol} submitted (~${est_notional:,.2f}).")
    return jsonify({"ok": True, "live": True, "order": result["order"], "preview": preview})


# ── orders: open / history / cancel (live) ───────────────────────────────────
@app.route("/api/orders")
def api_orders():
    guard = require_client()
    if guard:
        return guard
    open_only = request.args.get("open") == "1"
    try:
        return jsonify({"orders": client.get_orders(open_only=open_only)})
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e)}), 500


@app.route("/api/orders/cancel", methods=["POST"])
def api_orders_cancel():
    guard = require_client()
    if guard:
        return guard
    if TRADING_MODE != "live":
        return jsonify({"error": "Cancel is only available in live mode."}), 403
    order_id = (request.get_json(silent=True) or {}).get("id")
    if not order_id:
        return jsonify({"error": "order id required."}), 400
    try:
        client.cancel_order(order_id)
        return jsonify({"ok": True})
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e)}), 422


# ── strategies (Step 4) ──────────────────────────────────────────────────────
@app.route("/api/strategies", methods=["GET"])
def api_strategies():
    return jsonify({"strategies": strategies.list(), "trading_mode": TRADING_MODE})


@app.route("/api/strategies", methods=["POST"])
def api_strategies_add():
    try:
        s = strategies.add(request.get_json(silent=True) or {})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True, "strategy": s})


@app.route("/api/strategies/<sid>/toggle", methods=["POST"])
def api_strategies_toggle(sid):
    enabled = bool((request.get_json(silent=True) or {}).get("enabled"))
    try:
        return jsonify({"ok": True, "strategy": strategies.set_enabled(sid, enabled)})
    except KeyError:
        return jsonify({"error": "strategy not found"}), 404


@app.route("/api/strategies/<sid>/run", methods=["POST"])
def api_strategies_run(sid):
    guard = require_client()
    if guard:
        return guard
    try:
        return jsonify({"ok": True, "strategy": strategies.run_now(sid)})
    except KeyError:
        return jsonify({"error": "strategy not found"}), 404


@app.route("/api/strategies/<sid>", methods=["DELETE"])
def api_strategies_delete(sid):
    strategies.delete(sid)
    return jsonify({"ok": True})


if __name__ == "__main__":
    init_client()
    strategies.start()  # background scheduler for automated strategies
    port = int(os.getenv("PORT", "5000"))
    host = os.getenv("HOST", "127.0.0.1")
    if host != "127.0.0.1" and not _DASH_PASS:
        raise SystemExit(
            "ERROR: HOST is not 127.0.0.1 but DASHBOARD_PASSWORD is not set.\n"
            "Set DASHBOARD_PASSWORD in .env before exposing this app publicly."
        )
    print(f"\nWolf Grounds Alpha → http://{host}:{port}  (mode: {TRADING_MODE})")
    app.run(host=host, port=port, debug=False)
