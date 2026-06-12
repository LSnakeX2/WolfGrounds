"""
Wolf Grounds Stock Bot — Discord bridge.

An interactive Discord bot (discord.py) that talks to the running Stock Bot
Flask API. Query your portfolio, quotes, paper account, live orders, and
strategies — and place trades — from Discord. It reuses the backend's guardrails
(max-order cap, kill switch, live confirmation), so Discord is just a front end.

Run the Flask app first (python app.py), then this:  python discord_bridge.py

Config (.env):
  DISCORD_BOT_TOKEN        bot token (required)
  DISCORD_CHANNEL_ID       restrict commands to this channel id (recommended)
  DISCORD_ALLOWED_USER_IDS comma-separated user ids allowed to trade/control
  DISCORD_PREFIX           command prefix (default "!")
  STOCKBOT_API_URL         Flask API base (default http://127.0.0.1:5000)

SECURITY: this bot can place real trades in live mode. Always set
DISCORD_CHANNEL_ID and DISCORD_ALLOWED_USER_IDS so only you, in your channel,
can drive it.
"""

import asyncio
import os

import aiohttp
import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

API = os.getenv("STOCKBOT_API_URL", "http://127.0.0.1:5000").rstrip("/")
TOKEN = os.getenv("DISCORD_BOT_TOKEN")
CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "0") or 0)
ALLOWED = {
    int(x) for x in os.getenv("DISCORD_ALLOWED_USER_IDS", "").replace(" ", "").split(",") if x
}
PREFIX = os.getenv("DISCORD_PREFIX", "!")

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix=PREFIX, intents=intents, help_command=None)


# ── helpers ───────────────────────────────────────────────────────────────────
def usd(n):
    n = float(n or 0)
    return ("-$" if n < 0 else "$") + f"{abs(n):,.2f}"


async def api_get(path):
    async with aiohttp.ClientSession() as s:
        async with s.get(API + path) as r:
            return await r.json()


async def api_post(path, body=None):
    async with aiohttp.ClientSession() as s:
        async with s.post(API + path, json=body or {}) as r:
            return await r.json()


def is_authorized(ctx):
    """Control/trade commands: must be an allowed user (if the list is set)."""
    return not ALLOWED or ctx.author.id in ALLOWED


@bot.check
async def channel_gate(ctx):
    """Globally ignore commands outside the configured channel (if set)."""
    return CHANNEL_ID == 0 or ctx.channel.id == CHANNEL_ID


# ── read-only commands ────────────────────────────────────────────────────────
@bot.command(name="help")
async def help_cmd(ctx):
    e = discord.Embed(title="🐺 Wolf Grounds Stock Bot", color=0x7C5CFF)
    e.add_field(
        name="Read",
        value=f"`{PREFIX}portfolio` · `{PREFIX}quote AAPL MSFT` · `{PREFIX}paper` · "
        f"`{PREFIX}orders` · `{PREFIX}strategies` · `{PREFIX}status`",
        inline=False,
    )
    e.add_field(
        name="Trade & control (authorized users)",
        value=f"`{PREFIX}buy SYM QTY` · `{PREFIX}sell SYM QTY` · "
        f"`{PREFIX}enable ID` · `{PREFIX}disable ID` · `{PREFIX}kill` · `{PREFIX}resume`",
        inline=False,
    )
    await ctx.send(embed=e)


@bot.command()
async def status(ctx):
    s = await api_get("/api/status")
    if not s.get("logged_in"):
        await ctx.send(f"⚠️ Not logged in: {s.get('error')}")
        return
    k = "🔴 KILLED" if s.get("killed") else "🟢 armed"
    await ctx.send(
        f"Mode: **{s['trading_mode']}** · {k} · max order {usd(s.get('max_order_usd'))}"
    )


@bot.command()
async def portfolio(ctx):
    d = await api_get("/api/portfolio")
    if d.get("error"):
        await ctx.send(f"⚠️ {d['error']}")
        return
    sm = d["summary"]
    e = discord.Embed(title="📊 Portfolio", color=0x2ECC71 if sm["day_change"] >= 0 else 0xFF5C5C)
    e.add_field(name="Total", value=usd(sm["total_equity"]))
    e.add_field(name="Day", value=f"{usd(sm['day_change'])} ({sm['day_change_pct']:.2f}%)")
    e.add_field(name="Cash", value=usd(sm["cash"]))
    top = d["holdings"][:10]
    if top:
        e.add_field(
            name="Holdings",
            value="\n".join(
                f"`{h['symbol']:<5}` {h['quantity']:g} · {usd(h['equity'])} "
                f"({h['percent_change']:+.2f}%)"
                for h in top
            ),
            inline=False,
        )
    await ctx.send(embed=e)


@bot.command()
async def quote(ctx, *symbols):
    if not symbols:
        await ctx.send(f"Usage: `{PREFIX}quote AAPL MSFT`")
        return
    d = await api_get("/api/quote?symbols=" + ",".join(symbols))
    if d.get("error"):
        await ctx.send(f"⚠️ {d['error']}")
        return
    lines = [
        f"`{q['symbol']:<5}` {usd(q['price'])}  ({q['change']:+.2f}, {q['change_pct']:+.2f}%)"
        for q in d["quotes"]
    ]
    await ctx.send("\n".join(lines) or "No results.")


@bot.command()
async def paper(ctx):
    d = await api_get("/api/paper")
    if d.get("error"):
        await ctx.send(f"⚠️ {d['error']}")
        return
    e = discord.Embed(title="📝 Paper Account", color=0x7C5CFF)
    e.add_field(name="Equity", value=usd(d["total_equity"]))
    e.add_field(name="P&L", value=f"{usd(d['total_pnl'])} ({d['total_pnl_pct']:.2f}%)")
    e.add_field(name="Cash", value=usd(d["cash"]))
    if d["positions"]:
        e.add_field(
            name="Positions",
            value="\n".join(
                f"`{p['symbol']:<5}` {p['quantity']:g} · {usd(p['market_value'])} "
                f"({p['unrealized_pct']:+.2f}%)"
                for p in d["positions"][:10]
            ),
            inline=False,
        )
    await ctx.send(embed=e)


@bot.command()
async def orders(ctx):
    d = await api_get("/api/orders?open=1")
    if d.get("error"):
        await ctx.send(f"⚠️ {d['error']}")
        return
    if not d["orders"]:
        await ctx.send("No open orders.")
        return
    lines = [
        f"`{o['id'][:8]}` {o['side']} {o['quantity']:g} {o['symbol']} — {o['state']}"
        for o in d["orders"]
    ]
    await ctx.send("**Open orders:**\n" + "\n".join(lines))


@bot.command()
async def strategies(ctx):
    d = await api_get("/api/strategies")
    items = d.get("strategies", [])
    if not items:
        await ctx.send("No strategies.")
        return
    lines = []
    for s in items:
        flag = "🟢" if s["enabled"] else "⚪"
        if s["type"] == "dca":
            amt = usd(s["amount_usd"]) if s["amount_usd"] > 0 else f"{s['shares']:g}sh"
            detail = f"{s['side']} {amt} {s['symbol']}"
        else:
            detail = "rebalance " + ", ".join(f"{k} {v:.0%}" for k, v in s["targets"].items())
        lines.append(f"{flag} `{s['id']}` {s['type']}: {detail}")
    await ctx.send("**Strategies:**\n" + "\n".join(lines))


# ── trade & control commands (authorized) ─────────────────────────────────────
async def _place(ctx, side, symbol, qty):
    if not is_authorized(ctx):
        await ctx.send("⛔ You're not authorized to trade.")
        return
    symbol = symbol.upper()
    body = {"symbol": symbol, "side": side, "quantity": qty, "type": "market"}
    d = await api_post("/api/order", body)

    if d.get("trade"):  # paper filled immediately
        t = d["trade"]
        await ctx.send(f"📝 Paper {side} {t['quantity']:g} {symbol} @ {usd(t['price'])}")
        return
    if d.get("preview"):  # live → require confirmation reaction
        p = d["preview"]
        msg = await ctx.send(
            f"⚠️ **Confirm LIVE {side.upper()} {p['quantity']:g} {symbol}** "
            f"(~{usd(p['est_notional'])}). React ✅ within 30s."
        )
        await msg.add_reaction("✅")

        def check(reaction, user):
            return (
                user.id == ctx.author.id
                and str(reaction.emoji) == "✅"
                and reaction.message.id == msg.id
            )

        try:
            await bot.wait_for("reaction_add", timeout=30, check=check)
        except asyncio.TimeoutError:
            await ctx.send("⌛ Order cancelled (no confirmation).")
            return
        body["confirm"] = True
        d2 = await api_post("/api/order", body)
        if d2.get("ok"):
            o = d2["order"]
            await ctx.send(f"✅ LIVE order submitted — state: {o.get('state', 'pending')}")
        else:
            await ctx.send(f"✗ {d2.get('error', 'order failed')}")
        return
    await ctx.send(f"✗ {d.get('error', 'order failed')}")


@bot.command()
async def buy(ctx, symbol: str, qty: float):
    await _place(ctx, "buy", symbol, qty)


@bot.command()
async def sell(ctx, symbol: str, qty: float):
    await _place(ctx, "sell", symbol, qty)


@bot.command()
async def kill(ctx):
    if not is_authorized(ctx):
        await ctx.send("⛔ Not authorized.")
        return
    await api_post("/api/kill", {"enabled": True})
    await ctx.send("🔴 Kill switch **engaged** — live trading disabled.")


@bot.command()
async def resume(ctx):
    if not is_authorized(ctx):
        await ctx.send("⛔ Not authorized.")
        return
    await api_post("/api/kill", {"enabled": False})
    await ctx.send("🟢 Kill switch **disengaged** — live trading re-enabled.")


@bot.command()
async def enable(ctx, sid: str):
    if not is_authorized(ctx):
        await ctx.send("⛔ Not authorized.")
        return
    d = await api_post(f"/api/strategies/{sid}/toggle", {"enabled": True})
    await ctx.send("🟢 Strategy enabled." if d.get("ok") else f"✗ {d.get('error')}")


@bot.command()
async def disable(ctx, sid: str):
    if not is_authorized(ctx):
        await ctx.send("⛔ Not authorized.")
        return
    d = await api_post(f"/api/strategies/{sid}/toggle", {"enabled": False})
    await ctx.send("⚪ Strategy disabled." if d.get("ok") else f"✗ {d.get('error')}")


@bot.event
async def on_ready():
    print(f"✓ Discord bridge online as {bot.user} (API: {API})")


def main():
    if not TOKEN:
        raise SystemExit("DISCORD_BOT_TOKEN is not set in .env")
    bot.run(TOKEN)


if __name__ == "__main__":
    main()
