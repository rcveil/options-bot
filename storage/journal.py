"""
storage/journal.py
SQLite async trade log.
Stores both vertical spread and iron condor signals.
"""

import aiosqlite
import logging
from pathlib import Path

logger  = logging.getLogger(__name__)
DB_PATH = Path(__file__).parent / "db.sqlite3"

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS signals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol          TEXT    NOT NULL,
    strategy        TEXT    NOT NULL,
    direction       TEXT    NOT NULL,
    structure       TEXT    NOT NULL,
    -- Vertical spread fields
    sell_strike     REAL,
    buy_strike      REAL,
    spread_width    REAL,
    option_type     TEXT,
    -- Iron condor fields
    put_sell_strike  REAL,
    put_buy_strike   REAL,
    put_credit       REAL,
    put_credit_ratio REAL,
    call_sell_strike REAL,
    call_buy_strike  REAL,
    call_credit      REAL,
    call_credit_ratio REAL,
    wing_width       REAL,
    -- Butterfly fields
    lower_strike     REAL,
    body_strike      REAL,
    upper_strike     REAL,
    debit_ratio      REAL,
    max_profit       REAL,
    net_delta        REAL,
    -- Shared
    expiry          TEXT,
    dte             INTEGER,
    credit_debit    REAL,
    max_loss        REAL,
    ivr             REAL,
    vix             REAL,
    vix_regime      TEXT,
    pop             REAL,
    delta           REAL,
    theta           REAL,
    vega            REAL,
    iv              REAL,
    contracts       INTEGER,
    risk_dollars    REAL,
    vwap            REAL,
    rvol            REAL,
    rationale       TEXT,
    timestamp_et    TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
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

# Migration queries for upgrading existing databases
MIGRATIONS = [
    "ALTER TABLE signals ADD COLUMN spread_width REAL;",
    "ALTER TABLE signals ADD COLUMN put_sell_strike REAL;",
    "ALTER TABLE signals ADD COLUMN put_buy_strike REAL;",
    "ALTER TABLE signals ADD COLUMN put_credit REAL;",
    "ALTER TABLE signals ADD COLUMN put_credit_ratio REAL;",
    "ALTER TABLE signals ADD COLUMN call_sell_strike REAL;",
    "ALTER TABLE signals ADD COLUMN call_buy_strike REAL;",
    "ALTER TABLE signals ADD COLUMN call_credit REAL;",
    "ALTER TABLE signals ADD COLUMN call_credit_ratio REAL;",
    "ALTER TABLE signals ADD COLUMN wing_width REAL;",
    "ALTER TABLE signals ADD COLUMN lower_strike REAL;",
    "ALTER TABLE signals ADD COLUMN body_strike REAL;",
    "ALTER TABLE signals ADD COLUMN upper_strike REAL;",
    "ALTER TABLE signals ADD COLUMN debit_ratio REAL;",
    "ALTER TABLE signals ADD COLUMN max_profit REAL;",
    "ALTER TABLE signals ADD COLUMN net_delta REAL;",
]


async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(CREATE_SQL)
        for sql in MIGRATIONS:
            try:
                await db.execute(sql)
                await db.commit()
            except Exception:
                pass  # Column already exists
    logger.info(f"Database ready at {DB_PATH}")


async def log_signal(payload: dict) -> int:
    """
    Insert a signal. Works for both vertical spreads and iron condors.
    IC payloads include put_*/call_* keys; vertical spreads do not.
    Missing keys default to None via .get().
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            INSERT INTO signals (
                symbol, strategy, direction, structure,
                sell_strike, buy_strike, spread_width, option_type,
                put_sell_strike, put_buy_strike, put_credit, put_credit_ratio,
                call_sell_strike, call_buy_strike, call_credit, call_credit_ratio,
                wing_width,
                lower_strike, body_strike, upper_strike,
                debit_ratio, max_profit, net_delta,
                expiry, dte, credit_debit, max_loss,
                ivr, vix, vix_regime,
                pop, delta, theta, vega, iv,
                contracts, risk_dollars, vwap, rvol,
                rationale, timestamp_et
            ) VALUES (
                :symbol, :strategy, :direction, :structure,
                :sell_strike, :buy_strike, :spread_width, :option_type,
                :put_sell_strike, :put_buy_strike, :put_credit, :put_credit_ratio,
                :call_sell_strike, :call_buy_strike, :call_credit, :call_credit_ratio,
                :wing_width,
                :lower_strike, :body_strike, :upper_strike,
                :debit_ratio, :max_profit, :net_delta,
                :expiry, :dte, :credit_debit, :max_loss,
                :ivr, :vix, :vix_regime,
                :pop, :delta, :theta, :vega, :iv,
                :contracts, :risk_dollars, :vwap, :rvol,
                :rationale, :timestamp_et
            )
        """, {
            "symbol":           payload["symbol"],
            "strategy":         payload["strategy"],
            "direction":        payload["direction"],
            "structure":        payload["structure"],
            "sell_strike":      payload.get("sell_strike"),
            "buy_strike":       payload.get("buy_strike"),
            "spread_width":     payload.get("spread_width"),
            "option_type":      payload.get("option_type"),
            "put_sell_strike":  payload.get("put_sell_strike"),
            "put_buy_strike":   payload.get("put_buy_strike"),
            "put_credit":       payload.get("put_credit"),
            "put_credit_ratio": payload.get("put_credit_ratio"),
            "call_sell_strike": payload.get("call_sell_strike"),
            "call_buy_strike":  payload.get("call_buy_strike"),
            "call_credit":      payload.get("call_credit"),
            "call_credit_ratio":payload.get("call_credit_ratio"),
            "wing_width":       payload.get("wing_width"),
            "lower_strike":     payload.get("lower_strike"),
            "body_strike":      payload.get("body_strike"),
            "upper_strike":     payload.get("upper_strike"),
            "debit_ratio":      payload.get("debit_ratio"),
            "max_profit":       payload.get("max_profit"),
            "net_delta":        payload.get("net_delta"),
            "expiry":           payload["expiry"],
            "dte":              payload["dte"],
            "credit_debit":     payload["credit_debit"],
            "max_loss":         payload["max_loss"],
            "ivr":              payload["ivr"],
            "vix":              payload["vix"],
            "vix_regime":       payload["vix_regime"],
            "pop":              payload["pop"],
            "delta":            payload["delta"],
            "theta":            payload["theta"],
            "vega":             payload["vega"],
            "iv":               payload["iv"],
            "contracts":        payload["contracts"],
            "risk_dollars":     payload["risk_dollars"],
            "vwap":             payload["vwap"],
            "rvol":             payload["rvol"],
            "rationale":        payload["rationale"],
            "timestamp_et":     payload["timestamp_et"],
        })
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
            VALUES (?, datetime('now'), ?, ?, ?)
        """, (signal_id, close_price, pnl, exit_reason))
        await db.commit()
