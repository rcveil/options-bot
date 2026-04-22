"""
data/candles.py
Fetches 1-minute OHLCV bars using yfinance.

Key design:
- fetch_intraday_bars: always fetches ALL bars from 09:30 ET to now for today.
  This is correct — VWAP and ORB need the full session, not just recent bars.
  yfinance start/end params used explicitly for reliability.

- fetch_avg_volume: cached per trading day so it is only fetched once
  regardless of how many scans run. Avg volume does not change intraday.
"""

import logging
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)
ET     = ZoneInfo("America/New_York")

# Cache: {symbol: (date_str, avg_volume)}
# Resets automatically when date changes
_avg_volume_cache: dict[str, tuple[str, float]] = {}


async def fetch_intraday_bars(
    symbol:   str,
    interval: str = "1m",
) -> pd.DataFrame:
    """
    Fetch all 1-minute bars from 09:30 ET to now for today's session.
    Always fetches from open so VWAP and ORB are computed on full session data.

    Uses explicit start/end with prepost=False so we never get pre/after-market
    bars that would corrupt VWAP and ORB calculations.

    Returns DataFrame indexed by tz-aware datetime (ET).
    Returns empty DataFrame on failure or if market is closed.
    """
    today = date.today()
    start = today.strftime("%Y-%m-%d")
    # end is tomorrow to ensure we get all of today's bars
    end   = (today + timedelta(days=1)).strftime("%Y-%m-%d")

    logger.info(f"{symbol}: fetching {interval} bars for {start}")

    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(
            start    = start,
            end      = end,
            interval = interval,
            prepost  = False,   # regular session only — no pre/after market
        )

        if df is None or df.empty:
            logger.warning(
                f"{symbol}: yfinance returned empty — "
                f"market may be closed or symbol invalid"
            )
            return pd.DataFrame()

        # Normalise column names
        df.columns = [c.lower() for c in df.columns]
        df = df[["open", "high", "low", "close", "volume"]]

        # Ensure ET timezone
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC").tz_convert(ET)
        else:
            df.index = df.index.tz_convert(ET)

        # Filter to regular session only (09:30–16:00 ET)
        session_start = datetime(today.year, today.month, today.day,
                                  9, 30, tzinfo=ET)
        session_end   = datetime(today.year, today.month, today.day,
                                 16,  0, tzinfo=ET)
        df = df[(df.index >= session_start) & (df.index < session_end)]

        if df.empty:
            logger.warning(f"{symbol}: no bars within regular session hours")
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
    Fetch average daily volume over the past N trading days.
    Cached per trading day — only fetches once per session regardless
    of how many scans run during the day.
    Returns 0.0 if unavailable (graceful — RVOL will default to 1.0).
    """
    today_str = date.today().strftime("%Y-%m-%d")

    # Return cached value if already fetched today
    if symbol in _avg_volume_cache:
        cached_date, cached_vol = _avg_volume_cache[symbol]
        if cached_date == today_str:
            return cached_vol

    try:
        ticker  = yf.Ticker(symbol)
        # Fetch enough days to get lookback trading days
        hist    = ticker.history(period=f"{lookback * 2}d", interval="1d")

        if hist is None or hist.empty:
            logger.warning(f"{symbol}: no daily history from yfinance")
            _avg_volume_cache[symbol] = (today_str, 0.0)
            return 0.0

        hist.columns = [c.lower() for c in hist.columns]
        volumes = hist["volume"].dropna().tolist()

        if not volumes:
            _avg_volume_cache[symbol] = (today_str, 0.0)
            return 0.0

        avg = sum(volumes[-lookback:]) / len(volumes[-lookback:])
        avg = float(avg)

        _avg_volume_cache[symbol] = (today_str, avg)
        logger.info(f"{symbol}: avg daily volume = {avg:,.0f} (cached for today)")
        return avg

    except Exception as e:
        logger.warning(f"{symbol}: avg volume fetch failed — {e}")
        _avg_volume_cache[symbol] = (today_str, 0.0)
        return 0.0
