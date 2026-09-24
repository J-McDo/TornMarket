"""Signal output: console and optional Discord webhook."""

from __future__ import annotations

import json
import logging
import urllib.request

from .strategy import Signal

log = logging.getLogger(__name__)


def format_signal(acronym: str, name: str, sig: Signal) -> str:
    lines = [
        f"[{sig.action}] {acronym}{f' ({name})' if name else ''} @ ${sig.price:,.2f}  "
        f"score {sig.score:+.1f}, confidence {sig.confidence:.0%}"
    ]
    lines += [f"    - {r}" for r in sig.reasons]
    lines += [f"    ! {w}" for w in sig.warnings]
    return "\n".join(lines)


class Notifier:
    def __init__(self, discord_webhook: str = ""):
        self.discord_webhook = discord_webhook

    def send(self, message: str) -> None:
        print(message, flush=True)
        if self.discord_webhook:
            self._discord(message)

    def _discord(self, message: str) -> None:
        body = json.dumps({"content": f"```\n{message[:1900]}\n```"}).encode()
        req = urllib.request.Request(
            self.discord_webhook, data=body,
            headers={"Content-Type": "application/json", "User-Agent": "TornMarket"},
        )
        try:
            urllib.request.urlopen(req, timeout=10).close()
        except Exception as exc:  # never let a webhook failure stop the bot
            log.warning("Discord webhook failed: %s", exc)
