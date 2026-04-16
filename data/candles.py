"""
data/candles.py
Fetches 1-minute OHLCV bars using DXLinkStreamer + Candle events.

DXLinkStreamer uses TaskGroup internally which raises ExceptionGroup
when the stream ends. We handle this by wrapping the entire call in
a helper that catches ExceptionGroup via BaseException and inspects it.
Compatible with Python 3.11 and 3.12.
"""

import asyncio
import logging
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
from tastytrade import DXLinkStreamer
from tastytrade.dxfeed import Candle

from data.tastytrade import get_session

logger = logging.getLogger(__name__)
ET     = ZoneInfo("America/New_York")

CANDLE_TIMEOUT = 15.0


def _candle_symbol(symbol: str, interval: str = "1m") -> str:
    return f"{symbol}{{={interval}}}"


def _is_exception_group(e: BaseException) -> bool:
    """Check if exception is an ExceptionGroup (Python 3.11+)."""
    return type(e).__name__ in ("ExceptionGroup", "BaseExceptionGroup")


async def _stream_candles(
    session,
    candle_sym: str,
    from_ts:    int,
    from_time:  datetime,
    max_bars:   int = 80,
) -> list[dict]:
    """
    Core candle streaming logic.
    Separated so we can wrap it cleanly with timeout and error handling.
    """
    rows = []
    async with DXLinkStreamer(session) as streamer:
        await streamer.subscribe_candle(
            [candle_sym],
            from_time=from_ts,
        )
        async for candle in streamer.listen(Candle):
            if candle.event_symbol != candle_sym:
                continue

            bar_time = datetime.fromtimestamp(
                candle.time / 1000, tz=ET
            )
            if bar_time < from_time:
                continue

            rows.append({
                "datetime": bar_time,
                "open":     float(candle.open),
                "high":     float(candle.high),
                "low":      float(candle.low),
                "close":    float(candle.close),
                "volume":   float(candle.volume or 0),
            })

            if hasattr(candle, "snapshot_end") and candle.snapshot_end:
                break
            if len(rows) >= max_bars:
                break

    return rows


async def _collect_candles(
    session,
    candle_sym: str,
    from_ts:    int,
    from_time:  datetime,
    max_bars:   int = 80,
) -> list[dict]:
    """
    Wraps _stream_candles with timeout and broad exception handling.
    Returns whatever bars were collected before any error.
    """
    rows = []
    try:
        rows = await asyncio.wait_for(
            _stream_candles(session, candle_sym, from_ts, from_time, max_bars),
            timeout=CANDLE_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.warning(
            f"Candle timeout for {candle_sym} after {CANDLE_TIMEOUT}s"
        )
    except BaseException as e:
        # Catches ExceptionGroup from TaskGroup inside DXLinkStreamer
        # as well as regular exceptions — both are safe to swallow here
        if _is_exception_group(e):
            logger.warning(
                f"Candle stream TaskGroup ended for {candle_sym} "
                f"— {len(e.exceptions)} sub-exception(s). "
                f"Returning {len(rows)} bars collected."
            )
        else:
            logger.warning(
                f"Candle fetch error for {candle_sym}: "
                f"{type(e).__name__}: {e}"
            )

    return rows


async def fetch_intraday_bars(
    symbol:    str,
    from_time: datetime | None = None,
    interval:  str = "1m",
) -> pd.DataFrame:
    """
    Fetch 1-minute OHLCV bars from 09:30 ET to now.
    Returns empty DataFrame on failure.
    """
    session = await get_session()
    today   = date.today()

    if from_time is None:
        from_time = datetime(
            today.year, today.month, today.day,
            9, 30, 0, tzinfo=ET
        )

    from_ts    = int(from_time.timestamp() * 1000)
    candle_sym = _candle_symbol(symbol, interval)

    logger.info(
        f"{symbol}: fetching {interval} bars "
        f"from {from_time.strftime('%H:%M')} ET"
    )

    rows = await _collect_candles(
        session, candle_sym, from_ts, from_time
    )

    if not rows:
        logger.warning(
            f"{symbol}: no bars returned — "
            f"market may be closed or symbol unavailable"
        )
        return pd.DataFrame()

    df = pd.DataFrame(rows).set_index("datetime").sort_index()
    logger.info(
        f"{symbol}: {len(df)} bars — "
        f"{df.index[0].strftime('%H:%M')} to "
        f"{df.index[-1].strftime('%H:%M')} ET"
    )
    return df


async def fetch_avg_volume(
    symbol:   str,
    lookback: int = 20,
) -> float:
    """
    Fetch average daily volume over the past N trading days.
    Returns 0.0 if unavailable.
    """
    session   = await get_session()
    today     = date.today()
    from_date = today - timedelta(days=lookback * 2)
    from_dt   = datetime(
        from_date.year, from_date.month, from_date.day, tzinfo=ET
    )
    from_ts    = int(from_dt.timestamp() * 1000)
    candle_sym = _candle_symbol(symbol, "1d")

    rows = await _collect_candles(
        session, candle_sym, from_ts, from_dt, max_bars=lookback * 2
    )

    if not rows:
        logger.warning(f"{symbol}: no daily volume data")
        return 0.0

    volumes = [r["volume"] for r in rows if r["volume"] > 0]
    if not volumes:
        return 0.0

    avg = sum(volumes[-lookback:]) / len(volumes[-lookback:])
    logger.info(f"{symbol}: avg daily volume = {avg:,.0f}")
    return avg
