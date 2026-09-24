"""Walk-forward backtest of the strategy on stored bar closes (long only, all-in per stock)."""

from __future__ import annotations

from dataclasses import dataclass, field

from .api import Holding, Lot
from .config import StrategyConfig
from .strategy import BUY, SELL, evaluate, min_bars


@dataclass
class Trade:
    entry_ts: int
    entry: float
    exit_ts: int
    exit: float
    reason: str

    def return_pct(self, fee_pct: float) -> float:
        return (self.exit * (1 - fee_pct / 100) / self.entry - 1) * 100


@dataclass
class BacktestResult:
    trades: list[Trade] = field(default_factory=list)
    return_pct: float = 0.0
    buy_hold_pct: float = 0.0
    max_drawdown_pct: float = 0.0

    @property
    def win_rate(self) -> float:
        return 0.0 if not self.trades else sum(t.exit > t.entry for t in self.trades) / len(self.trades)


def run_backtest(bars: list[tuple[int, float]], cfg: StrategyConfig, capital: float = 1.0) -> BacktestResult:
    result = BacktestResult()
    if len(bars) < min_bars(cfg) + 1:
        return result

    cash, shares = capital, 0.0
    entry_ts, entry_px, peak = 0, 0.0, 0.0
    equity_peak, max_dd = capital, 0.0
    closes = [p for _, p in bars]

    for i in range(min_bars(cfg) - 1, len(bars)):
        ts, price = bars[i]
        holding = None
        if shares:
            peak = max(peak, price)
            holding = Holding(0, 1, [Lot(1, entry_px, entry_ts)])
        sig = evaluate(closes[: i + 1], cfg, holding, peak)

        if not shares and sig.action == BUY:
            shares, cash = cash / price, 0.0
            entry_ts, entry_px, peak = ts, price, price
        elif shares and sig.action == SELL:
            cash = shares * price * (1 - cfg.sell_fee_pct / 100)
            shares = 0.0
            result.trades.append(Trade(entry_ts, entry_px, ts, price, sig.reasons[0]))

        equity = cash + shares * price
        equity_peak = max(equity_peak, equity)
        max_dd = max(max_dd, (1 - equity / equity_peak) * 100)

    final = cash + shares * closes[-1] * (1 - cfg.sell_fee_pct / 100)
    result.return_pct = (final / capital - 1) * 100
    start = closes[min_bars(cfg) - 1]
    result.buy_hold_pct = (closes[-1] * (1 - cfg.sell_fee_pct / 100) / start - 1) * 100
    result.max_drawdown_pct = max_dd
    return result
