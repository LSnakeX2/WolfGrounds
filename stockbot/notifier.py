"""
Lightweight Discord push notifications for Wolf Grounds Alpha.

Posts short messages to a Discord channel via an incoming webhook. Uses only
the standard library (no discord.py needed) so the Flask server can fire alerts
— strategy runs, live fills — without the interactive bot being up.

Set DISCORD_WEBHOOK_URL in .env to enable; if unset, notify() is a no-op.
"""

import json
import threading
import urllib.request


def make_notifier(webhook_url):
    if not webhook_url:
        return lambda text: None

    def _post(text):
        try:
            data = json.dumps({"content": text[:1900]}).encode()
            req = urllib.request.Request(
                webhook_url, data=data, headers={"Content-Type": "application/json"}
            )
            urllib.request.urlopen(req, timeout=5)
        except Exception as e:  # noqa: BLE001
            print(f"[notify] failed: {e}")

    def notify(text):
        # Fire-and-forget so a slow webhook never blocks trading.
        threading.Thread(target=_post, args=(text,), daemon=True).start()

    return notify
