"""Minimal Torn City API client for the stock market endpoints.

Endpoints used (Torn API v1, read-only):
  * ``torn/?selections=stocks`` - every stock's current price, market cap, shares, benefit
  * ``user/?selections=stocks`` - the key owner's holdings and buy transactions

The Torn API cannot place trades; this bot only reads data and emits signals.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from dataclasses import dataclass, field

BASE_URL = "https://api.torn.com"

# Torn error codes that are worth retrying after a pause.
RETRYABLE_CODES = {5, 8, 9, 17}  # too many requests, temp IP ban, API disabled, backend error


class TornAPIError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(f"Torn API error {code}: {message}")
        self.code = code
        self.message = message


@dataclass
class StockBenefit:
    type: str = ""
    frequency: int = 0
    requirement: int = 0
    description: str = ""


@dataclass
class StockQuote:
    stock_id: int
    name: str
    acronym: str
    price: float
    market_cap: float = 0.0
    total_shares: int = 0
    investors: int = 0
    benefit: StockBenefit = field(default_factory=StockBenefit)


@dataclass
class Lot:
    shares: int
    bought_price: float
    time_bought: int


@dataclass
class Holding:
    stock_id: int
    total_shares: int
    lots: list[Lot] = field(default_factory=list)

    @property
    def avg_cost(self) -> float:
        shares = sum(l.shares for l in self.lots)
        if not shares:
            return 0.0
        return sum(l.shares * l.bought_price for l in self.lots) / shares

    @property
    def first_bought(self) -> int:
        return min((l.time_bought for l in self.lots), default=0)


class RateLimiter:
    """Sliding-window limiter. Torn allows 100 requests/minute per user across all keys."""

    def __init__(self, max_calls: int = 60, period: float = 60.0):
        self.max_calls = max_calls
        self.period = period
        self._calls: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            while self._calls and now - self._calls[0] >= self.period:
                self._calls.popleft()
            if len(self._calls) >= self.max_calls:
                time.sleep(self.period - (now - self._calls[0]))
                self._calls.popleft()
            self._calls.append(time.monotonic())


def parse_stocks(payload: dict) -> list[StockQuote]:
    quotes = []
    for key, s in (payload.get("stocks") or {}).items():
        b = s.get("benefit") or {}
        quotes.append(
            StockQuote(
                stock_id=int(s.get("stock_id", key)),
                name=s.get("name", ""),
                acronym=s.get("acronym", str(key)),
                price=float(s.get("current_price", 0) or 0),
                market_cap=float(s.get("market_cap", 0) or 0),
                total_shares=int(s.get("total_shares", 0) or 0),
                investors=int(s.get("investors", 0) or 0),
                benefit=StockBenefit(
                    type=b.get("type", ""),
                    frequency=int(b.get("frequency", 0) or 0),
                    requirement=int(b.get("requirement", 0) or 0),
                    description=b.get("description", ""),
                ),
            )
        )
    return sorted(quotes, key=lambda q: q.stock_id)


def parse_holdings(payload: dict) -> dict[int, Holding]:
    holdings: dict[int, Holding] = {}
    for key, s in (payload.get("stocks") or {}).items():
        stock_id = int(s.get("stock_id", key))
        lots = [
            Lot(
                shares=int(t.get("shares", 0)),
                bought_price=float(t.get("bought_price", 0)),
                time_bought=int(t.get("time_bought", 0)),
            )
            for t in (s.get("transactions") or {}).values()
        ]
        holdings[stock_id] = Holding(stock_id, int(s.get("total_shares", 0)), lots)
    return holdings


class TornClient:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = BASE_URL,
        timeout: float = 15.0,
        max_retries: int = 3,
        limiter: RateLimiter | None = None,
        comment: str = "TornMarket",
    ):
        if not api_key:
            raise ValueError("A Torn API key is required (set TORN_API_KEY)")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.limiter = limiter or RateLimiter()
        self.comment = comment

    def _get(self, path: str, selections: str) -> dict:
        query = urllib.parse.urlencode(
            {"selections": selections, "key": self.api_key, "comment": self.comment}
        )
        url = f"{self.base_url}/{path}?{query}"
        delay = 2.0
        for attempt in range(self.max_retries + 1):
            self.limiter.acquire()
            try:
                with urllib.request.urlopen(url, timeout=self.timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
                if attempt == self.max_retries:
                    raise
                time.sleep(delay)
                delay *= 2
                continue
            err = data.get("error")
            if err:
                code, msg = int(err.get("code", -1)), err.get("error", "unknown")
                if code in RETRYABLE_CODES and attempt < self.max_retries:
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise TornAPIError(code, msg)
            return data
        raise RuntimeError("unreachable")

    def market(self) -> list[StockQuote]:
        return parse_stocks(self._get("torn/", "stocks"))

    def portfolio(self) -> dict[int, Holding]:
        return parse_holdings(self._get("user/", "stocks"))
