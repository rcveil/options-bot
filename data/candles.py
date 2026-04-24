"""
data/candles.py
Fetches 1-minute OHLCV bars using yfinance.

Fixes:
- Semaphore reduced to 1 (sequential fetches) — yfinance rate limits
  aggressively on cloud IPs; sequential is the only reliable approach
- asyncio.to_thread() prevents blocking the event loop
- TzCache directed to /tmp to avoid Fly.io filesystem conflict
- Avg volume cached per trading day
- Retry with longer backoff on 429
"""

import asyncio
import logging
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

# Direct TzCache to /tmp to avoid Fly.io '/root/.cache' conflict
try:
    yf.set_tz_cache_location("/tmp/yfinance_tz_cache")
except Exception:
    pass

logger = logging.getLogger(__name__)
ET     = ZoneInfo("America/New_York")

# Sequential fetches only — yfinance rate limits hard on cloud IPs
_semaphore = asyncio.Semaphore(1)

# Avg volume cache: {symbol: (date_str, avg_volume)}
_avg_volume_cache: dict = {}

MAX_RETRIES = 4
RETRY_DELAY = 8.0   # seconds * attempt number


def _fetch_bars_sync(symbol, start, end, interval):
    ticker = yf.Ticker(symbol)
    return ticker.history(start=start, end=end, interval=interval, prepost=False)


def _fetch_volume_sync(symbol, period):
    ticker = yf.Ticker(symbol)
    return ticker.history(period=period, interval="1d")


async def fetch_intraday_bars(symbol: str, interval: str = "1m"):
    today = date.today()
    start = today.strftime("%Y-%m-%d")
    end   = (today + timedelta(days=1)).strftime("%Y-%m-%d")
    logger.info(f"{symbol}: fetching {interval} bars")

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with _semaphore:
                df = await asyncio.to_thread(
                    _fetch_bars_sync, symbol, start, end, interval
                )

            if df is None or df.empty:
                logger.warning(f"{symbol}: yfinance returned empty — market may be closed")
                return pd.DataFrame()

            df.columns = [c.lower() for c in df.columns]
            df = df[["open", "high", "low", "close", "volume"]]

            if df.index.tz is None:
                df.index = df.index.tz_localize("UTC").tz_convert(ET)
            else:
                df.index = df.index.tz_convert(ET)

            s_start = datetime(today.year, today.month, today.day,  9, 30, tzinfo=ET)
            s_end   = datetime(today.year, today.month, today.day, 16,  0, tzinfo=ET)
            df = df[(df.index >= s_start) & (df.index < s_end)]

            if df.empty:
                logger.warning(f"{symbol}: no bars in regular session")
                return pd.DataFrame()

            logger.info(
                f"{symbol}: {len(df)} bars — "
                f"{df.index[0].strftime('%H:%M')} to "
                f"{df.index[-1].strftime('%H:%M')} ET"
            )
            return df

        except Exception as e:
            msg = str(e)
            if any(x in msg for x in ("Too Many Requests", "Rate limited", "429")):
                if attempt < MAX_RETRIES:
                    wait = RETRY_DELAY * attempt
                    logger.warning(
                        f"{symbol}: rate limited — waiting {wait:.0f}s "
                        f"(attempt {attempt}/{MAX_RETRIES})"
                    )
                    await asyncio.sleep(wait)
                else:
                    logger.error(f"{symbol}: rate limited after {MAX_RETRIES} attempts — skipping")
                    return pd.DataFrame()
            else:
                logger.error(f"{symbol}: yfinance error — {e}")
                return pd.DataFrame()

    return pd.DataFrame()


async def fetch_avg_volume(symbol: str, lookback: int = 20) -> float:
    today_str = date.today().strftime("%Y-%m-%d")

    if symbol in _avg_volume_cache:
        cached_date, cached_vol = _avg_volume_cache[symbol]
        if cached_date == today_str:
            return cached_vol

    try:
        async with _semaphore:
            hist = await asyncio.to_thread(
                _fetch_volume_sync, symbol, f"{lookback * 2}d"
            )

        if hist is None or hist.empty:
            _avg_volume_cache[symbol] = (today_str, 0.0)
            return 0.0

        hist.columns = [c.lower() for c in hist.columns]
        volumes = hist["volume"].dropna().tolist()
        if not volumes:
            _avg_volume_cache[symbol] = (today_str, 0.0)
            return 0.0

        avg = float(sum(volumes[-lookback:]) / len(volumes[-lookback:]))
        _avg_volume_cache[symbol] = (today_str, avg)
        logger.info(f"{symbol}: avg volume = {avg:,.0f} (cached)")
        return avg

    except Exception as e:
        logger.warning(f"{symbol}: avg volume fetch failed — {e}")
        _avg_volume_cache[symbol] = (today_str, 0.0)
        return 0.0
