"""
storage/journal.py
SQLite async trade log.
spread_width stored alongside sell/buy strikes for accurate late entry checks.
"""

import aiosqlite
import logging
from pathlib import Path

logger  = logging.getLogger(__name__)
DB_PATH = Path(__file__).parent / "db.sqlite3"

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS signals (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol         TEXT    NOT NULL,
    strategy       TEXT    NOT NULL,
    direction      TEXT    NOT NULL,
    structure      TEXT    NOT NULL,
    sell_strike    REAL,
    buy_strike     REAL,
    spread_width   REAL,
    option_type    TEXT,
    expiry         TEXT,
    dte            INTEGER,
    credit_debit   REAL,
    max_loss       REAL,
    ivr            REAL,
    vix            REAL,
    vix_regime     TEXT,
    pop            REAL,
    delta          REAL,
    theta          REAL,
    vega           REAL,
    iv             REAL,
    contracts      INTEGER,
    risk_dollars   REAL,
    vwap           REAL,
    rvol           REAL,
    rationale      TEXT,
    timestamp_et   TEXT,
    created_at     TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS outcomes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id   INTEGER REFERENCES signals(id),
    closed_at   TEXT,
    close_price REAL,
    pnl         REAL,
    exit_reason TEXT
);
"""

# Migration: add spread_width column if upgrading from old schema
MIGRATE_SQL = """
ALTER TABLE signals ADD COLUMN spread_width REAL;
"""


async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(CREATE_SQL)
        # Add spread_width column if it doesn't exist (migration for existing DBs)
        try:
            await db.execute(MIGRATE_SQL)
            await db.commit()
            logger.info("Migrated: added spread_width column to signals table")
        except Exception:
            pass  # Column already exists — expected on fresh or already-migrated DBs
    logger.info(f"Database ready at {DB_PATH}")


async def log_signal(payload: dict) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            INSERT INTO signals (
                symbol, strategy, direction, structure,
                sell_strike, buy_strike, spread_width, option_type,
                expiry, dte, credit_debit, max_loss,
                ivr, vix, vix_regime,
                pop, delta, theta, vega, iv,
                contracts, risk_dollars, vwap, rvol,
                rationale, timestamp_et
            ) VALUES (
                :symbol, :strategy, :direction, :structure,
                :sell_strike, :buy_strike, :spread_width, :option_type,
                :expiry, :dte, :credit_debit, :max_loss,
                :ivr, :vix, :vix_regime,
                :pop, :delta, :theta, :vega, :iv,
                :contracts, :risk_dollars, :vwap, :rvol,
                :rationale, :timestamp_et
            )
        """, payload)
        await db.commit()
        return cursor.lastrowid


async def get_latest_signal(symbol: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            SELECT * FROM signals
            WHERE  symbol = ?
            ORDER  BY created_at DESC
            LIMIT  1
        """, (symbol,))
        row = await cursor.fetchone()
        return dict(row) if row else None


async def log_outcome(
    signal_id:   int,
    close_price: float,
    pnl:         float,
    exit_reason: str,
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO outcomes
                (signal_id, closed_at, close_price, pnl, exit_reason)
            VALUES
                (?, datetime('now'), ?, ?, ?)
        """, (signal_id, close_price, pnl, exit_reason))
        await db.commit()
