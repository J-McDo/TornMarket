"""Harvest loop: poll the market, store snapshots, evaluate and announce signal changes."""

from __future__ import annotations

import logging
import time

from .api import Holding, TornAPIError, TornClient
from .config import Config
from .indicators import resample
from .notify import Notifier, format_signal
from .storage import Store
from .strategy import HOLD, Signal, evaluate

log = logging.getLogger(__name__)


def evaluate_stock(store: Store, cfg: Config, stock_id: int, holding: Holding | None,
                   benefit_requirement: int = 0) -> Signal | None:
    points = store.history(stock_id)
    if not points:
        return None
    bars = resample(points, cfg.strategy.bar_seconds)
    closes = [p for _, p in bars]
    peak = None
    if holding and holding.first_bought:
        peak = max((p for ts, p in points if ts >= holding.first_bought), default=None)
    return evaluate(closes, cfg.strategy, holding, peak, benefit_requirement)


def evaluate_all(store: Store, cfg: Config, holdings: dict[int, Holding]
                 ) -> list[tuple[dict, Signal]]:
    results = []
    for stock_id, meta in store.stocks().items():
        if cfg.watchlist and meta["acronym"] not in cfg.watchlist and stock_id not in holdings:
            continue
        sig = evaluate_stock(store, cfg, stock_id, holdings.get(stock_id),
                             meta["benefit_requirement"] or 0)
        if sig:
            results.append((meta, sig))
    return results


class Bot:
    def __init__(self, cfg: Config, client: TornClient, store: Store, notifier: Notifier):
        self.cfg = cfg
        self.client = client
        self.store = store
        self.notifier = notifier
        self._portfolio_ok = True

    def fetch_holdings(self) -> dict[int, Holding]:
        if not self._portfolio_ok:
            return {}
        try:
            return self.client.portfolio()
        except TornAPIError as exc:
            if exc.code == 16:  # key access level too low
                log.warning("API key cannot read your portfolio; position exits disabled")
                self._portfolio_ok = False
                return {}
            raise

    def tick(self, now: int | None = None) -> list[tuple[dict, Signal]]:
        now = now or int(time.time())
        quotes = self.client.market()
        self.store.save_snapshot(quotes, now)
        holdings = self.fetch_holdings()

        changed = []
        for meta, sig in evaluate_all(self.store, self.cfg, holdings):
            if sig.action == self.store.last_signal(meta["stock_id"]):
                continue
            self.store.record_signal(meta["stock_id"], now, sig.action, sig.score, sig.price, sig.reasons)
            if sig.action != HOLD:
                self.notifier.send(format_signal(meta["acronym"], meta["name"], sig))
                changed.append((meta, sig))
        return changed

    def run_forever(self) -> None:
        log.info("Polling Torn stock market every %ss (bars of %ss)",
                 self.cfg.poll_seconds, self.cfg.strategy.bar_seconds)
        while True:
            started = time.monotonic()
            try:
                self.tick()
            except TornAPIError as exc:
                if exc.code in (2, 10, 13, 18):  # bad/paused/inactive key: fatal
                    raise
                log.error("%s", exc)
            except Exception:
                log.exception("tick failed")
            time.sleep(max(1.0, self.cfg.poll_seconds - (time.monotonic() - started)))
