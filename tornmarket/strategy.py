"""Signal engine: combines standard technical flags into a BUY / SELL / HOLD call.

Each flag adds (bullish) or subtracts (bearish) points; the net score is compared
with the configured thresholds. For stocks you already hold, risk-management
exits (stop-loss, take-profit, trailing stop) override the technical score.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import indicators as ind
from .api import Holding
from .config import StrategyConfig

BUY, SELL, HOLD = "BUY", "SELL", "HOLD"


@dataclass
class Signal:
    action: str
    score: float
    price: float
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    indicators: dict[str, float] = field(default_factory=dict)

    @property
    def confidence(self) -> float:
        return min(1.0, abs(self.score) / 8.0)


def min_bars(cfg: StrategyConfig) -> int:
    return max(cfg.ema_slow + cfg.macd_signal, cfg.sma_trend, cfg.bb_period, cfg.rsi_period + 1) + 1


def crossed_above(a_prev, a_now, b_prev, b_now) -> bool:
    return None not in (a_prev, a_now, b_prev, b_now) and a_prev <= b_prev and a_now > b_now


def crossed_below(a_prev, a_now, b_prev, b_now) -> bool:
    return None not in (a_prev, a_now, b_prev, b_now) and a_prev >= b_prev and a_now < b_now


def technical_score(closes: list[float], cfg: StrategyConfig) -> tuple[float, list[str], dict[str, float]]:
    """Score the latest bar. Positive = bullish, negative = bearish."""
    score = 0.0
    reasons: list[str] = []
    price = closes[-1]

    ema_f = ind.ema(closes, cfg.ema_fast)
    ema_s = ind.ema(closes, cfg.ema_slow)
    trend = ind.sma(closes, cfg.sma_trend)
    rsi = ind.rsi(closes, cfg.rsi_period)
    _, _, hist = ind.macd(closes, cfg.ema_fast, cfg.ema_slow, cfg.macd_signal)
    lower, mid, upper = ind.bollinger(closes, cfg.bb_period, cfg.bb_width)
    roc = ind.roc(closes, cfg.roc_period)

    def add(points: float, why: str) -> None:
        nonlocal score
        score += points
        reasons.append(f"{'+' if points > 0 else ''}{points:g} {why}")

    # 1. Trend: fast/slow EMA crossover (golden / death cross) or ongoing alignment.
    if crossed_above(ema_f[-2], ema_f[-1], ema_s[-2], ema_s[-1]):
        add(2, f"EMA{cfg.ema_fast} crossed above EMA{cfg.ema_slow} (golden cross)")
    elif crossed_below(ema_f[-2], ema_f[-1], ema_s[-2], ema_s[-1]):
        add(-2, f"EMA{cfg.ema_fast} crossed below EMA{cfg.ema_slow} (death cross)")
    elif ema_f[-1] > ema_s[-1]:
        add(1, f"EMA{cfg.ema_fast} above EMA{cfg.ema_slow} (uptrend)")
    else:
        add(-1, f"EMA{cfg.ema_fast} below EMA{cfg.ema_slow} (downtrend)")

    # 2. Regime filter: price relative to long SMA.
    if price > trend[-1]:
        add(1, f"price above SMA{cfg.sma_trend} (bull regime)")
    else:
        add(-1, f"price below SMA{cfg.sma_trend} (bear regime)")

    # 3. Momentum: MACD histogram zero-line cross, else direction of histogram.
    if hist[-2] is not None and hist[-2] <= 0 < hist[-1]:
        add(1.5, "MACD crossed above signal line")
    elif hist[-2] is not None and hist[-2] >= 0 > hist[-1]:
        add(-1.5, "MACD crossed below signal line")
    elif hist[-2] is not None and hist[-1] > hist[-2] and hist[-1] > 0:
        add(0.5, "MACD momentum strengthening")
    elif hist[-2] is not None and hist[-1] < hist[-2] and hist[-1] < 0:
        add(-0.5, "MACD momentum weakening")

    # Oscillators are traded with the trend: buy dips in uptrends, sell rallies in
    # downtrends. Counter-trend extremes carry little weight unless they reverse.
    uptrend = ema_f[-1] > ema_s[-1]

    # 4. RSI: exits from extremes are reversal triggers; extremes themselves are
    #    pullback entries when aligned with the trend.
    r_prev, r = rsi[-2], rsi[-1]
    if r_prev is not None and r_prev < cfg.rsi_oversold <= r:
        add(2 if uptrend else 1.5, f"RSI recovered above {cfg.rsi_oversold:g} (oversold bounce)")
    elif r_prev is not None and r_prev > cfg.rsi_overbought >= r:
        add(-1.5 if uptrend else -2, f"RSI fell back below {cfg.rsi_overbought:g} (overbought reversal)")
    elif r < cfg.rsi_oversold:
        if uptrend:
            add(1.5, f"RSI {r:.1f} oversold in uptrend (buy the dip)")
        else:
            add(0.5, f"RSI {r:.1f} oversold, counter-trend")
    elif r > cfg.rsi_overbought:
        if uptrend:
            add(-0.5, f"RSI {r:.1f} overbought, counter-trend")
        else:
            add(-1.5, f"RSI {r:.1f} overbought in downtrend (sell the rally)")

    # 5. Bollinger Bands: a stretch beyond the band is a pullback entry with the
    #    trend; against the trend it signals breakdown/breakout momentum, so ignore it.
    if price < lower[-1] and uptrend:
        add(1, "price below lower Bollinger Band in uptrend")
    elif price > upper[-1] and not uptrend:
        add(-1, "price above upper Bollinger Band in downtrend")

    # 6. Rate of change confirmation.
    if roc[-1] is not None and abs(roc[-1]) >= 2:
        add(0.5 if roc[-1] > 0 else -0.5, f"{cfg.roc_period}-bar ROC {roc[-1]:+.1f}%")

    values = {
        "price": price, "ema_fast": ema_f[-1], "ema_slow": ema_s[-1], "sma_trend": trend[-1],
        "rsi": r, "macd_hist": hist[-1], "bb_lower": lower[-1], "bb_mid": mid[-1],
        "bb_upper": upper[-1], "roc": roc[-1],
    }
    return score, reasons, {k: v for k, v in values.items() if v is not None}


def risk_exit(price: float, holding: Holding, peak: float, cfg: StrategyConfig) -> str | None:
    """Return a reason string if a position-level exit rule is hit."""
    cost = holding.avg_cost
    if not cost:
        return None
    net = price * (1 - cfg.sell_fee_pct / 100)
    pnl = (net / cost - 1) * 100
    if pnl <= -cfg.stop_loss_pct:
        return f"stop-loss hit ({pnl:+.1f}% vs avg cost {cost:,.2f})"
    if pnl >= cfg.take_profit_pct:
        return f"take-profit hit ({pnl:+.1f}% after {cfg.sell_fee_pct:g}% fee)"
    if peak > cost and price <= peak * (1 - cfg.trailing_stop_pct / 100) and net > cost:
        return f"trailing stop: {(price / peak - 1) * 100:.1f}% off peak {peak:,.2f}, locking gain"
    return None


def evaluate(
    closes: list[float],
    cfg: StrategyConfig,
    holding: Holding | None = None,
    peak_since_buy: float | None = None,
    benefit_requirement: int = 0,
) -> Signal:
    price = closes[-1]
    if len(closes) < min_bars(cfg):
        return Signal(HOLD, 0.0, price, [f"warming up: {len(closes)}/{min_bars(cfg)} bars"])

    score, reasons, values = technical_score(closes, cfg)
    held = holding is not None and holding.total_shares > 0

    action = HOLD
    if score >= cfg.buy_threshold:
        action = BUY
    elif score <= cfg.sell_threshold:
        action = SELL

    signal = Signal(action, score, price, reasons, indicators=values)
    if held:
        exit_reason = risk_exit(price, holding, peak_since_buy or price, cfg)
        if exit_reason:
            signal.action = SELL
            signal.reasons.insert(0, exit_reason)
        if (signal.action == SELL and cfg.protect_benefit_blocks and benefit_requirement
                and holding.total_shares >= benefit_requirement):
            spare = holding.total_shares - benefit_requirement
            signal.warnings.append(
                f"you hold a dividend benefit block ({benefit_requirement:,} shares); "
                f"sell at most {spare:,} to keep it"
            )
    elif action == SELL:
        # Nothing to sell: surface as an 'avoid' rather than an actionable sell.
        signal.warnings.append("not held - treat as AVOID")
    return signal
