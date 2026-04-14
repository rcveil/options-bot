"""
data/candles.py
Fetches 1-minute OHLCV bars using DXLinkStreamer + Candle events.
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


def _candle_symbol(symbol: str, interval: str = "1m") -> str:
    return f"{symbol}{{={interval}}}"


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

    today = date.today()
    if from_time is None:
        from_time = datetime(
            today.year, today.month, today.day,
            9, 30, 0, tzinfo=ET
        )

    from_ts    = int(from_time.timestamp() * 1000)
    candle_sym = _candle_symbol(symbol, interval)

    logger.info(f"{symbol}: fetching {interval} bars from {from_time.strftime('%H:%M')} ET")

    rows = []
    try:
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

                if hasattr(candle, 'snapshot_end') and candle.snapshot_end:
                    break
                if len(rows) >= 80:
                    break

    except asyncio.TimeoutError:
        logger.warning(f"{symbol}: candle fetch timed out")
    except Exception as e:
        logger.error(f"{symbol}: candle fetch failed — {e}")
        return pd.DataFrame()

    if not rows:
        logger.warning(f"{symbol}: no candle bars returned — market may be closed or symbol invalid")
        return pd.DataFrame()

    df = pd.DataFrame(rows).set_index("datetime").sort_index()
    logger.info(f"{symbol}: {len(df)} bars fetched (first={df.index[0].strftime('%H:%M')}, last={df.index[-1].strftime('%H:%M')})")
    return df


async def fetch_avg_volume(
    symbol:   str,
    lookback: int = 20,
) -> float:
    """
    Fetch average daily volume over the past N trading days.
    Returns 0.0 if unavailable.
    """
    session    = await get_session()
    today      = date.today()
    from_date  = today - timedelta(days=lookback * 2)
    from_dt    = datetime(from_date.year, from_date.month, from_date.day, tzinfo=ET)
    from_ts    = int(from_dt.timestamp() * 1000)
    candle_sym = _candle_symbol(symbol, "1d")
    volumes    = []

    try:
        async with DXLinkStreamer(session) as streamer:
            await streamer.subscribe_candle(
                [candle_sym],
                from_time=from_ts,
            )
            async for candle in streamer.listen(Candle):
                if candle.event_symbol != candle_sym:
                    continue
                if candle.volume:
                    volumes.append(float(candle.volume))
                if hasattr(candle, 'snapshot_end') and candle.snapshot_end:
                    break
                if len(volumes) >= lookback * 2:
                    break
    except Exception as e:
        logger.warning(f"{symbol}: avg volume fetch failed — {e}")
        return 0.0

    if not volumes:
        logger.warning(f"{symbol}: no daily volume data returned")
        return 0.0

    avg = sum(volumes[-lookback:]) / len(volumes[-lookback:])
    logger.info(f"{symbol}: avg daily volume = {avg:,.0f}")
    return avg
