"""Configuration: defaults, optionally overridden by a TOML file and env vars."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path


@dataclass
class StrategyConfig:
    bar_seconds: int = 900          # evaluate on 15-minute bars
    ema_fast: int = 12
    ema_slow: int = 26
    macd_signal: int = 9
    sma_trend: int = 50             # long-term regime filter
    rsi_period: int = 14
    rsi_oversold: float = 30.0
    rsi_overbought: float = 70.0
    bb_period: int = 20
    bb_width: float = 2.0
    roc_period: int = 10
    buy_threshold: float = 3.0      # net score needed to fire BUY
    sell_threshold: float = -3.0    # net score needed to fire SELL
    # Position risk management (applied only to stocks you hold)
    stop_loss_pct: float = 8.0
    take_profit_pct: float = 15.0
    trailing_stop_pct: float = 6.0
    sell_fee_pct: float = 0.1       # Torn charges 0.1% when selling shares
    protect_benefit_blocks: bool = True


@dataclass
class Config:
    api_key: str = ""
    db_path: str = "tornmarket.db"
    poll_seconds: int = 60
    max_calls_per_minute: int = 60
    discord_webhook: str = ""
    watchlist: list[str] = field(default_factory=list)  # acronyms; empty = all stocks
    strategy: StrategyConfig = field(default_factory=StrategyConfig)


def _apply(obj, values: dict) -> None:
    known = {f.name for f in fields(obj)}
    for key, value in values.items():
        if key not in known:
            raise ValueError(f"Unknown config key: {key}")
        if key == "strategy":
            _apply(obj.strategy, value)
        else:
            setattr(obj, key, value)


def load_config(path: str | Path | None = None) -> Config:
    cfg = Config()
    if path is None and Path("tornmarket.toml").exists():
        path = "tornmarket.toml"
    if path:
        with open(path, "rb") as fh:
            _apply(cfg, tomllib.load(fh))
    cfg.api_key = os.environ.get("TORN_API_KEY", cfg.api_key)
    cfg.discord_webhook = os.environ.get("TORN_DISCORD_WEBHOOK", cfg.discord_webhook)
    cfg.watchlist = [s.upper() for s in cfg.watchlist]
    return cfg
