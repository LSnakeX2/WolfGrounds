"""
Wolf Grounds Stock Bot — Flask backend (STEP 1: read-only dashboard).

Serves a local web dashboard showing your real Robinhood portfolio, live
quotes, and day P&L. It does NOT place any trades — order endpoints arrive in
a later step, behind explicit confirmation + a max-order cap + a kill switch.

SAFETY: run this on localhost only. Anyone who can reach this server can read
your account. Never expose it to the public internet without real auth.
"""

import os
from flask import Flask, jsonify, request, send_from_directory
from dotenv import load_dotenv

from robinhood_client import RobinhoodClient
from paper_engine import PaperBroker, PaperError

load_dotenv()

HERE = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.join(HERE, "frontend")

app = Flask(__name__, static_folder=None)

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
        return _place_paper(symbol, side, quantity, order_type, limit_price, last)

    # ── LIVE ────────────────────────────────────────────────────────────────
    if trading_killed:
        return jsonify({"error": "Kill switch engaged — live trading is disabled."}), 423

    # Hard caps enforced server-side regardless of the UI.
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
        order = client.place_live_order(symbol, side, quantity, order_type, limit_price)
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": f"Order rejected: {e}"}), 422

    return jsonify({"ok": True, "live": True, "order": order, "preview": preview})


def _place_paper(symbol, side, quantity, order_type, limit_price, last):
    """Simulated fill against the live price (paper mode)."""
    if order_type == "limit":
        marketable = last <= limit_price if side == "buy" else last >= limit_price
        if not marketable:
            return (
                jsonify(
                    {
                        "error": f"Limit not marketable: {symbol} at ${last:,.2f}, "
                        f"limit ${limit_price:,.2f}. (Resting orders come later.)"
                    }
                ),
                422,
            )
        fill_price = limit_price
    else:
        fill_price = last

    try:
        trade = paper.place_order(symbol, side, quantity, order_type, fill_price)
    except PaperError as e:
        return jsonify({"error": str(e)}), 422

    return jsonify({"ok": True, "trade": trade, "paper": paper.state(client.get_prices)})


if __name__ == "__main__":
    init_client()
    port = int(os.getenv("PORT", "5000"))
    print(f"\nWolf Grounds Stock Bot → http://127.0.0.1:{port}  (mode: {TRADING_MODE})")
    # host=127.0.0.1 keeps this bound to localhost only. Do not change to 0.0.0.0
    # without adding authentication — it can read your brokerage account.
    app.run(host="127.0.0.1", port=port, debug=False)
