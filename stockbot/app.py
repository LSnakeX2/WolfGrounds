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
        }
    )


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
    """Place an order.

    Routed by TRADING_MODE:
      read_only -> 403 (no trading)
      paper     -> simulated fill against the live market price
      live      -> 403 for now (real orders arrive in Step 3, with guardrails)
    """
    if TRADING_MODE == "read_only":
        return jsonify({"error": "Trading is disabled (TRADING_MODE=read_only)."}), 403
    if TRADING_MODE == "live":
        return jsonify({"error": "Live trading not yet implemented (Step 3)."}), 403
    if TRADING_MODE != "paper":
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

    # Derive the fill price from a live quote.
    quotes = client.get_quotes([symbol])
    if not quotes:
        return jsonify({"error": f"No market data for {symbol}."}), 400
    q = quotes[0]
    last = q["price"]

    if order_type == "limit":
        try:
            limit_price = float(data.get("limit_price") or 0)
        except (TypeError, ValueError):
            return jsonify({"error": "Invalid limit price."}), 400
        if limit_price <= 0:
            return jsonify({"error": "Limit price required for limit orders."}), 400
        # Immediate-or-reject: fill only if the limit is marketable right now.
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
        order_type = "market"
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
