"""Command line interface: ``python -m tornmarket <command>``."""

from __future__ import annotations

import argparse
import csv
import logging
import sys
import time

from .api import RateLimiter, TornClient
from .backtest import run_backtest
from .bot import Bot, evaluate_all
from .config import load_config
from .indicators import resample
from .notify import Notifier, format_signal
from .storage import Store
from .strategy import HOLD


def make_client(cfg) -> TornClient:
    return TornClient(cfg.api_key, limiter=RateLimiter(cfg.max_calls_per_minute))


def cmd_collect(cfg, store, args) -> int:
    quotes = make_client(cfg).market()
    store.save_snapshot(quotes, int(time.time()))
    for q in quotes:
        print(f"{q.acronym:>5}  ${q.price:>10,.2f}  {q.name}")
    return 0


def cmd_run(cfg, store, args) -> int:
    Bot(cfg, make_client(cfg), store, Notifier(cfg.discord_webhook)).run_forever()
    return 0


def cmd_signals(cfg, store, args) -> int:
    holdings = make_client(cfg).portfolio() if args.portfolio else {}
    results = evaluate_all(store, cfg, holdings)
    if not results:
        print("No price history yet - run `collect` or `run` first.")
    for meta, sig in sorted(results, key=lambda r: -abs(r[1].score)):
        if args.all or sig.action != HOLD:
            print(format_signal(meta["acronym"], meta["name"], sig))
    return 0


def cmd_backtest(cfg, store, args) -> int:
    for stock_id, meta in store.stocks().items():
        if args.stock and meta["acronym"] != args.stock.upper():
            continue
        bars = resample(store.history(stock_id), cfg.strategy.bar_seconds)
        res = run_backtest(bars, cfg.strategy)
        if not res.trades and not res.buy_hold_pct:
            print(f"{meta['acronym']:>5}  not enough history ({len(bars)} bars)")
            continue
        print(f"{meta['acronym']:>5}  strategy {res.return_pct:+7.2f}%  buy&hold {res.buy_hold_pct:+7.2f}%  "
              f"trades {len(res.trades):3d}  win {res.win_rate:4.0%}  maxDD {res.max_drawdown_pct:5.1f}%")
    return 0


def cmd_import(cfg, store, args) -> int:
    """Import historical prices from CSV with columns: stock_id,timestamp,price."""
    with open(args.csv, newline="") as fh:
        rows = [(int(r["stock_id"]), int(r["timestamp"]), float(r["price"])) for r in csv.DictReader(fh)]
    store.add_prices(rows)
    print(f"Imported {len(rows)} price points")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tornmarket", description="Torn City stock market signal bot")
    parser.add_argument("-c", "--config", help="path to TOML config (default ./tornmarket.toml)")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("collect", help="fetch one market snapshot and store it")
    sub.add_parser("run", help="poll continuously and announce new BUY/SELL signals")
    p = sub.add_parser("signals", help="evaluate current signals from stored history")
    p.add_argument("--all", action="store_true", help="include HOLD")
    p.add_argument("--portfolio", action="store_true", help="fetch holdings for stop-loss/take-profit exits")
    p = sub.add_parser("backtest", help="backtest the strategy on stored history")
    p.add_argument("--stock", help="acronym, e.g. TSB")
    p = sub.add_parser("import", help="import historical prices from CSV (stock_id,timestamp,price)")
    p.add_argument("csv")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_config(args.config)
    if args.command in ("collect", "run") or getattr(args, "portfolio", False):
        if not cfg.api_key:
            print("Set TORN_API_KEY (or api_key in tornmarket.toml)", file=sys.stderr)
            return 2
    store = Store(cfg.db_path)
    try:
        return {"collect": cmd_collect, "run": cmd_run, "signals": cmd_signals,
                "backtest": cmd_backtest, "import": cmd_import}[args.command](cfg, store, args)
    except KeyboardInterrupt:
        return 0
    finally:
        store.close()
