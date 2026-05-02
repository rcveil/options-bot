"""
data/candles.py
Fetches 1-minute OHLCV bars using yfinance.

Back to yfinance — Tastytrade candle stream not returning bars.
Rate limiting handled via:
- Sequential fetches only (no concurrency)
- 3-second delay between symbols
- Retry with 10s backoff on 429
- TzCache to /tmp
- Avg volume cached per session
"""

import asyncio
import logging
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

# TzCache to /tmp to avoid Fly.io filesystem issues
try:
    yf.set_tz_cache_location("/tmp/yfinance_tz_cache")
except Exception:
    pass

logger = logging.getLogger(__name__)
ET     = ZoneInfo("America/New_York")

_avg_volume_cache: dict = {}

MAX_RETRIES     = 4
RETRY_DELAY     = 10.0   # seconds between retries
INTER_SYMBOL_DELAY = 3.0 # seconds between different symbols


def _fetch_bars_sync(symbol: str, start: str, end: str, interval: str) -> pd.DataFrame:
    """Synchronous yfinance fetch."""
    ticker = yf.Ticker(symbol)
    return ticker.history(start=start, end=end, interval=interval, prepost=False)


def _fetch_volume_sync(symbol: str, period: str) -> pd.DataFrame:
    """Synchronous yfinance daily volume fetch."""
    ticker = yf.Ticker(symbol)
    return ticker.history(period=period, interval="1d")


async def fetch_intraday_bars(
    symbol:   str,
    interval: str = "1m",
) -> pd.DataFrame:
    """
    Fetch 1-min bars from market open to now.
    Sequential only — no concurrency.
    3-second delay enforced externally (main.py should call symbols sequentially).
    """
    today = date.today()
    start = today.strftime("%Y-%m-%d")
    end   = (today + timedelta(days=1)).strftime("%Y-%m-%d")

    logger.info(f"{symbol}: fetching {interval} bars")

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            # Run in thread to avoid blocking event loop
            df = await asyncio.to_thread(
                _fetch_bars_sync, symbol, start, end, interval
            )

            if df is None or df.empty:
                logger.warning(f"{symbol}: yfinance returned empty")
                return pd.DataFrame()

            # Normalize columns
            df.columns = [c.lower() for c in df.columns]
            
            # Ensure we have OHLCV columns
            if not all(c in df.columns for c in ["open", "high", "low", "close", "volume"]):
                logger.error(f"{symbol}: missing OHLCV columns")
                return pd.DataFrame()
            
            df = df[["open", "high", "low", "close", "volume"]]

            # Ensure ET timezone
            if df.index.tz is None:
                df.index = df.index.tz_localize("UTC").tz_convert(ET)
            else:
                df.index = df.index.tz_convert(ET)

            # Clip to regular session 09:30–16:00 ET
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
            
            # Add 3-second delay after successful fetch to avoid rate limiting next symbol
            await asyncio.sleep(INTER_SYMBOL_DELAY)
            
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
                    logger.error(
                        f"{symbol}: rate limited after {MAX_RETRIES} attempts — skipping"
                    )
                    return pd.DataFrame()
            else:
                logger.error(f"{symbol}: yfinance error — {e}")
                return pd.DataFrame()

    return pd.DataFrame()


async def fetch_avg_volume(symbol: str, lookback: int = 20) -> float:
    """
    Average daily volume over past N trading days.
    Cached per trading day — only fetched once per session.
    """
    today_str = date.today().strftime("%Y-%m-%d")

    if symbol in _avg_volume_cache:
        cached_date, cached_vol = _avg_volume_cache[symbol]
        if cached_date == today_str:
            return cached_vol

    try:
        hist = await asyncio.to_thread(
            _fetch_volume_sync, symbol, f"{lookback * 2}d"
        )

        if hist is None or hist.empty:
            _avg_volume_cache[symbol] = (today_str, 0.0)
            return 0.0

        hist.columns = [c.lower() for c in hist.columns]
        
        if "volume" not in hist.columns:
            _avg_volume_cache[symbol] = (today_str, 0.0)
            return 0.0
        
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
