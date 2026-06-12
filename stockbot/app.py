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

load_dotenv()

HERE = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.join(HERE, "frontend")

app = Flask(__name__, static_folder=None)

# Single shared client for this local single-user dashboard.
client = None
login_error = None
TRADING_MODE = os.getenv("TRADING_MODE", "read_only").lower()


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


# Placeholder for STEP 2/3. Trading stays disabled until we deliberately
# build the order layer with guardrails.
@app.route("/api/order", methods=["POST"])
def api_order():
    return (
        jsonify(
            {
                "error": "Trading is disabled. This build is read-only "
                f"(TRADING_MODE={TRADING_MODE})."
            }
        ),
        403,
    )


if __name__ == "__main__":
    init_client()
    port = int(os.getenv("PORT", "5000"))
    print(f"\nWolf Grounds Stock Bot → http://127.0.0.1:{port}  (mode: {TRADING_MODE})")
    # host=127.0.0.1 keeps this bound to localhost only. Do not change to 0.0.0.0
    # without adding authentication — it can read your brokerage account.
    app.run(host="127.0.0.1", port=port, debug=False)
