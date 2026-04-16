"""
data/candles.py
Fetches 1-minute OHLCV bars using yfinance (Yahoo Finance).

yfinance is reliable, free, and returns clean pandas DataFrames.
DXLinkStreamer candle subscriptions are unreliable for historical bars
so we use yfinance instead.
"""

import logging
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)
ET     = ZoneInfo("America/New_York")


def _today_session_start() -> datetime:
    today = date.today()
    return datetime(today.year, today.month, today.day, 9, 30, tzinfo=ET)


async def fetch_intraday_bars(
    symbol:    str,
    from_time: datetime | None = None,
    interval:  str = "1m",
) -> pd.DataFrame:
    """
    Fetch 1-minute OHLCV bars for today's session using yfinance.
    Returns DataFrame with columns: open, high, low, close, volume
    Indexed by datetime (ET, timezone-aware).
    Returns empty DataFrame on failure.
    """
    if from_time is None:
        from_time = _today_session_start()

    logger.info(
        f"{symbol}: fetching {interval} bars via yfinance "
        f"from {from_time.strftime('%H:%M')} ET"
    )

    try:
        ticker = yf.Ticker(symbol)
        # period="1d" gives today's intraday bars at 1m resolution
        df = ticker.history(period="1d", interval=interval)

        if df.empty:
            logger.warning(
                f"{symbol}: yfinance returned empty — "
                f"market may be closed"
            )
            return pd.DataFrame()

        # Normalise column names to lowercase
        df.columns = [c.lower() for c in df.columns]
        df = df[["open", "high", "low", "close", "volume"]]

        # Ensure timezone-aware index in ET
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC").tz_convert(ET)
        else:
            df.index = df.index.tz_convert(ET)

        # Filter to bars from our window onwards
        df = df[df.index >= from_time]

        if df.empty:
            logger.warning(
                f"{symbol}: no bars after {from_time.strftime('%H:%M')} ET"
            )
            return pd.DataFrame()

        logger.info(
            f"{symbol}: {len(df)} bars — "
            f"{df.index[0].strftime('%H:%M')} to "
            f"{df.index[-1].strftime('%H:%M')} ET"
        )
        return df

    except Exception as e:
        logger.error(f"{symbol}: yfinance fetch failed — {e}")
        return pd.DataFrame()


async def fetch_avg_volume(
    symbol:   str,
    lookback: int = 20,
) -> float:
    """
    Fetch average daily volume over the past N trading days using yfinance.
    Returns 0.0 if unavailable.
    """
    try:
        ticker  = yf.Ticker(symbol)
        hist    = ticker.history(period=f"{lookback * 2}d", interval="1d")

        if hist.empty:
            logger.warning(f"{symbol}: no daily history from yfinance")
            return 0.0

        hist.columns = [c.lower() for c in hist.columns]
        volumes = hist["volume"].dropna().tolist()

        if not volumes:
            return 0.0

        avg = sum(volumes[-lookback:]) / len(volumes[-lookback:])
        logger.info(f"{symbol}: avg daily volume = {avg:,.0f}")
        return float(avg)

    except Exception as e:
        logger.warning(f"{symbol}: avg volume fetch failed — {e}")
        return 0.0
