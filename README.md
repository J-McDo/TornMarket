# TornMarket

A bot that harvests the [Torn City](https://www.torn.com) stock market API, stores price history,
and fires **BUY / SELL** signals from standard technical-analysis flags, with position-level risk
exits for the stocks you hold.

It only reads data. The Torn API can't place trades, so you act on the signals yourself in-game,
which also keeps the bot within Torn's scripting rules.

No third-party dependencies: Python 3.11+ standard library only.

## Quick start

```bash
export TORN_API_KEY=your_key_here        # Torn → Settings → API Keys ("Limited" access recommended)
python -m tornmarket collect             # one snapshot, prints all prices
python -m tornmarket run                 # poll every 60s, announce new signals
python -m tornmarket signals --all       # current call for every stock from stored history
python -m tornmarket signals --portfolio # include stop-loss / take-profit checks on your holdings
python -m tornmarket backtest            # replay the strategy over stored history
python -m tornmarket import history.csv  # seed history (CSV: stock_id,timestamp,price)
```

Copy `tornmarket.example.toml` to `tornmarket.toml` to tune settings, set a watchlist, or add a
Discord webhook (`TORN_DISCORD_WEBHOOK`) for alerts.

Indicators run on 15-minute bars by default, so the strategy needs about 60 bars (~15 hours of
polling) before it leaves the warm-up phase. To start sooner, import history with `import` or
lower `bar_seconds`.

## Endpoints harvested

| Endpoint | Used for |
|---|---|
| `torn/?selections=stocks` | price, market cap, shares, investors, dividend benefit block per stock |
| `user/?selections=stocks` | your holdings and buy lots (avg cost, first buy time) for risk exits |

The client rate-limits itself to `max_calls_per_minute` (Torn's hard cap is 100/min across all
your keys). It retries with backoff on Torn error codes 5/8/9/17 and stops on key errors.

## Signal model

Each stock is scored on its latest bar. Positive points are bullish and negative are bearish.
A score ≥ `buy_threshold` (3) fires **BUY**, and ≤ `sell_threshold` (−3) fires **SELL**.

| Flag | Points |
|---|---|
| EMA12/EMA26 golden / death cross | ±2 (±1 while simply aligned) |
| Price vs SMA50 (regime filter) | ±1 |
| MACD crosses its signal line | ±1.5 (±0.5 for strengthening / weakening histogram) |
| RSI exits oversold / overbought (reversal trigger) | up to ±2 |
| RSI oversold / overbought | ±1.5 with the trend, ±0.5 counter-trend |
| Close beyond Bollinger Band (20, 2σ) | ±1, only with the trend (buy dips in uptrends, sell rallies in downtrends) |
| 10-bar rate of change ≥ 2% | ±0.5 |

Oscillators are traded **with** the trend. In a crash, "oversold" is weak evidence rather than a
buy, so trend and mean-reversion flags don't cancel each other out.

**Risk exits** (stocks you hold, from your buy lots) override the score:
- stop-loss at −8% vs average cost
- take-profit at +15% after Torn's 0.1% sell fee
- trailing stop 6% off the peak since the first buy, only while still in profit

**Benefit-block guard:** if a SELL would drop you below a stock's dividend benefit requirement,
the signal warns you and tells you how many shares you can sell safely.

The bot announces a stock only when its signal *changes*, so it won't repeat the same BUY every
minute. Every change is logged to the `signals` table in SQLite.

## Tests

```bash
python -m unittest -v
```

## Disclaimer

These are heuristic signals on an in-game market. Backtest on your own collected data and tune
the thresholds before trusting them.
