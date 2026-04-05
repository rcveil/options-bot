"""
data/candles.py
Fetches 1-minute OHLCV bars from Tastytrade for a given symbol.
Used by signals/indicators.py for VWAP, EMA, ORB, RVOL.
"""

import logging
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
from tastytrade.market_data import get_candles, CandleType

from data.tastytrade import get_session

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")


async def fetch_intraday_bars(
    symbol:       str,
    from_time:    datetime | None = None,
    bar_size:     str = "1m",
) -> pd.DataFrame:
    """
    Fetch 1-minute OHLCV bars for today's session from open to now.

    Returns a DataFrame with columns:
        datetime, open, high, low, close, volume
    Indexed by datetime (ET, timezone-aware).

    Args:
        symbol:    Ticker e.g. "NVDA"
        from_time: Start of fetch window (defaults to 09:30 ET today)
        bar_size:  Candle size — "1m" default
    """
    session = await get_session()

    today = date.today()
    if from_time is None:
        from_time = datetime(
            today.year, today.month, today.day,
            9, 30, 0, tzinfo=ET
        )

    to_time = datetime.now(ET)

    logger.debug(f"Fetching {bar_size} bars for {symbol} "
                 f"from {from_time.strftime('%H:%M')} to "
                 f"{to_time.strftime('%H:%M')} ET")

    try:
        candles = await get_candles(
            session,
            symbol,
            interval    = bar_size,
            start_time  = from_time,
            end_time    = to_time,
        )
    except Exception as e:
        logger.error(f"Failed to fetch candles for {symbol}: {e}")
        return pd.DataFrame()

    if not candles:
        logger.warning(f"No candles returned for {symbol}")
        return pd.DataFrame()

    rows = []
    for c in candles:
        rows.append({
            "datetime": c.time.astimezone(ET),
            "open":     float(c.open),
            "high":     float(c.high),
            "low":      float(c.low),
            "close":    float(c.close),
            "volume":   float(c.volume),
        })

    df = pd.DataFrame(rows).set_index("datetime").sort_index()
    logger.debug(f"{symbol}: {len(df)} bars fetched")
    return df


async def fetch_avg_volume(
    symbol:   str,
    lookback: int = 20,
) -> float:
    """
    Fetch average daily volume over the past N trading days.
    Used to compute RVOL (relative volume).

    Returns average volume as a float (0.0 if unavailable).
    """
    session = await get_session()

    today     = date.today()
    from_date = today - timedelta(days=lookback * 2)   # buffer for weekends

    try:
        candles = await get_candles(
            session,
            symbol,
            interval   = "1d",
            start_time = datetime(from_date.year, from_date.month,
                                  from_date.day, tzinfo=ET),
            end_time   = datetime(today.year, today.month,
                                  today.day, 23, 59, tzinfo=ET),
        )
    except Exception as e:
        logger.error(f"Avg volume fetch failed for {symbol}: {e}")
        return 0.0

    if not candles:
        return 0.0

    volumes = [float(c.volume) for c in candles[-lookback:] if c.volume]
    return sum(volumes) / len(volumes) if volumes else 0.0
