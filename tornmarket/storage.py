"""SQLite persistence for price snapshots and emitted signals."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .api import StockQuote

SCHEMA = """
CREATE TABLE IF NOT EXISTS stocks (
    stock_id INTEGER PRIMARY KEY,
    name TEXT,
    acronym TEXT,
    benefit_type TEXT,
    benefit_requirement INTEGER,
    benefit_frequency INTEGER,
    benefit_description TEXT
);
CREATE TABLE IF NOT EXISTS prices (
    stock_id INTEGER NOT NULL,
    ts INTEGER NOT NULL,
    price REAL NOT NULL,
    market_cap REAL,
    total_shares INTEGER,
    investors INTEGER,
    PRIMARY KEY (stock_id, ts)
);
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_id INTEGER NOT NULL,
    ts INTEGER NOT NULL,
    action TEXT NOT NULL,
    score REAL,
    price REAL,
    reasons TEXT
);
CREATE INDEX IF NOT EXISTS idx_signals_stock ON signals (stock_id, ts);
"""


class Store:
    def __init__(self, path: str | Path = "tornmarket.db"):
        self.conn = sqlite3.connect(str(path))
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def save_snapshot(self, quotes: list[StockQuote], ts: int) -> None:
        with self.conn:
            self.conn.executemany(
                "INSERT OR REPLACE INTO stocks VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (q.stock_id, q.name, q.acronym, q.benefit.type, q.benefit.requirement,
                     q.benefit.frequency, q.benefit.description)
                    for q in quotes
                ],
            )
            self.conn.executemany(
                "INSERT OR REPLACE INTO prices VALUES (?, ?, ?, ?, ?, ?)",
                [(q.stock_id, ts, q.price, q.market_cap, q.total_shares, q.investors) for q in quotes],
            )

    def add_prices(self, rows: list[tuple[int, int, float]]) -> None:
        """Bulk insert (stock_id, ts, price) rows, e.g. imported history."""
        with self.conn:
            self.conn.executemany(
                "INSERT OR IGNORE INTO stocks (stock_id, acronym) VALUES (?, ?)",
                {(sid, str(sid)) for sid, _, _ in rows},
            )
            self.conn.executemany(
                "INSERT OR REPLACE INTO prices (stock_id, ts, price) VALUES (?, ?, ?)", rows
            )

    def stocks(self) -> dict[int, dict]:
        rows = self.conn.execute("SELECT * FROM stocks ORDER BY stock_id").fetchall()
        cols = ["stock_id", "name", "acronym", "benefit_type", "benefit_requirement",
                "benefit_frequency", "benefit_description"]
        return {r[0]: dict(zip(cols, r)) for r in rows}

    def history(self, stock_id: int, since: int = 0) -> list[tuple[int, float]]:
        return self.conn.execute(
            "SELECT ts, price FROM prices WHERE stock_id = ? AND ts >= ? ORDER BY ts",
            (stock_id, since),
        ).fetchall()

    def last_signal(self, stock_id: int) -> str | None:
        row = self.conn.execute(
            "SELECT action FROM signals WHERE stock_id = ? ORDER BY ts DESC, id DESC LIMIT 1",
            (stock_id,),
        ).fetchone()
        return row[0] if row else None

    def record_signal(self, stock_id: int, ts: int, action: str, score: float,
                      price: float, reasons: list[str]) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO signals (stock_id, ts, action, score, price, reasons) VALUES (?, ?, ?, ?, ?, ?)",
                (stock_id, ts, action, score, price, "; ".join(reasons)),
            )
