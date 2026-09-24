"""Technical indicators on plain Python lists.

Every function returns a list aligned with its input; positions without enough
history are ``None``.
"""

from __future__ import annotations

import math

Series = list[float | None]


def resample(points: list[tuple[int, float]], bar_seconds: int) -> list[tuple[int, float]]:
    """Collapse (ts, price) snapshots into bars, keeping the last price in each bar."""
    bars: dict[int, float] = {}
    for ts, price in points:
        bars[ts - ts % bar_seconds] = price
    return sorted(bars.items())


def sma(values: list[float], period: int) -> Series:
    out: Series = [None] * len(values)
    total = 0.0
    for i, v in enumerate(values):
        total += v
        if i >= period:
            total -= values[i - period]
        if i >= period - 1:
            out[i] = total / period
    return out


def ema(values: list[float], period: int) -> Series:
    """EMA seeded with the SMA of the first ``period`` values."""
    out: Series = [None] * len(values)
    if len(values) < period:
        return out
    k = 2 / (period + 1)
    prev = sum(values[:period]) / period
    out[period - 1] = prev
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def _ema_optional(values: Series, period: int) -> Series:
    start = next((i for i, v in enumerate(values) if v is not None), len(values))
    tail = ema([v for v in values[start:]], period)  # type: ignore[misc]
    return [None] * start + tail


def rsi(values: list[float], period: int = 14) -> Series:
    """Wilder's RSI."""
    out: Series = [None] * len(values)
    if len(values) <= period:
        return out
    gains = losses = 0.0
    for i in range(1, period + 1):
        change = values[i] - values[i - 1]
        gains += max(change, 0)
        losses += max(-change, 0)
    avg_gain, avg_loss = gains / period, losses / period

    def value(g: float, l: float) -> float:
        if l == 0:
            return 100.0 if g > 0 else 50.0
        return 100 - 100 / (1 + g / l)

    out[period] = value(avg_gain, avg_loss)
    for i in range(period + 1, len(values)):
        change = values[i] - values[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(change, 0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-change, 0)) / period
        out[i] = value(avg_gain, avg_loss)
    return out


def macd(values: list[float], fast: int = 12, slow: int = 26, signal: int = 9
         ) -> tuple[Series, Series, Series]:
    """Returns (macd line, signal line, histogram)."""
    fast_e, slow_e = ema(values, fast), ema(values, slow)
    line: Series = [f - s if f is not None and s is not None else None for f, s in zip(fast_e, slow_e)]
    sig = _ema_optional(line, signal)
    hist: Series = [m - s if m is not None and s is not None else None for m, s in zip(line, sig)]
    return line, sig, hist


def bollinger(values: list[float], period: int = 20, width: float = 2.0
              ) -> tuple[Series, Series, Series]:
    """Returns (lower, middle, upper) bands using population standard deviation."""
    mid = sma(values, period)
    lower: Series = [None] * len(values)
    upper: Series = [None] * len(values)
    for i in range(period - 1, len(values)):
        window = values[i - period + 1 : i + 1]
        m = mid[i]
        sd = math.sqrt(sum((v - m) ** 2 for v in window) / period)
        lower[i], upper[i] = m - width * sd, m + width * sd
    return lower, mid, upper


def roc(values: list[float], period: int = 10) -> Series:
    """Rate of change in percent."""
    return [
        (values[i] / values[i - period] - 1) * 100 if i >= period and values[i - period] else None
        for i in range(len(values))
    ]


def volatility(values: list[float], period: int = 20) -> Series:
    """Rolling standard deviation of bar-to-bar returns, in percent."""
    out: Series = [None] * len(values)
    rets = [0.0] + [(values[i] / values[i - 1] - 1) * 100 if values[i - 1] else 0.0
                    for i in range(1, len(values))]
    for i in range(period, len(values)):
        window = rets[i - period + 1 : i + 1]
        mean = sum(window) / period
        out[i] = math.sqrt(sum((r - mean) ** 2 for r in window) / period)
    return out
