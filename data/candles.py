"""
data/candles.py
Fetches 1-minute OHLCV bars using Tastytrade DXLinkStreamer.

Replaces yfinance entirely — yfinance is rate-limited on cloud IPs.
Tastytrade's own streamer is the correct data source since we are
already authenticated.

Key SDK facts (verified from source):
- subscribe_candle(symbols, interval, start_time) takes plain symbols
  e.g. ["SPY"], not pre-formatted strings
- interval format: "1m", "5m", "1h" etc.
- start_time: datetime object for the start of the data range
- extended_trading_hours=False (default) = regular session only
- Candle fields: time (ms), open, high, low, close, volume, index

Avg volume cached per trading day — fetched once per session.
"""

import asyncio
import logging
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from collections import defaultdict

import pandas as pd

from data.tastytrade import get_session

logger = logging.getLogger(__name__)
ET     = ZoneInfo("America/New_York")

CANDLE_TIMEOUT   = 20.0    # seconds to wait for snapshot
MAX_BARS         = 100     # max 1m bars to collect (~100 min)

# Avg volume cache: {symbol: (date_str, avg_volume)}
_avg_volume_cache: dict = {}


def _session_start_today() -> datetime:
    today = date.today()
    return datetime(today.year, today.month, today.day, 9, 30, tzinfo=ET)


async def _stream_candles(
    symbol:     str,
    interval:   str,
    start_time: datetime,
    max_bars:   int = MAX_BARS,
) -> list[dict]:
    """
    Subscribe to candle stream and collect bars.
    Returns list of bar dicts.
    """
    from tastytrade import DXLinkStreamer
    from tastytrade.dxfeed import Candle

    session = await get_session()
    rows    = []

    try:
        async with asyncio.timeout(CANDLE_TIMEOUT):
            async with DXLinkStreamer(session) as streamer:
                await streamer.subscribe_candle(
                    symbols                = [symbol],
                    interval               = interval,
                    start_time             = start_time,
                    extended_trading_hours = False,   # regular session only
                )

                async for candle in streamer.listen(Candle):
                    bar_time = datetime.fromtimestamp(
                        candle.time / 1000, tz=ET
                    )

                    # Skip bars before session start
                    if bar_time < start_time:
                        continue

                    # Skip bars with zero open (malformed)
                    if not candle.open or float(candle.open) == 0:
                        continue

                    rows.append({
                        "datetime": bar_time,
                        "open":     float(candle.open),
                        "high":     float(candle.high),
                        "low":      float(candle.low),
                        "close":    float(candle.close),
                        "volume":   float(candle.volume or 0),
                    })

                    if len(rows) >= max_bars:
                        break

    except asyncio.TimeoutError:
        logger.warning(
            f"{symbol}: candle stream timed out after {CANDLE_TIMEOUT}s "
            f"— returning {len(rows)} bars collected"
        )
    except BaseException as e:
        if type(e).__name__ in ("ExceptionGroup", "BaseExceptionGroup"):
            logger.warning(
                f"{symbol}: candle stream ended (TaskGroup) "
                f"— {len(rows)} bars collected"
            )
        else:
            logger.warning(
                f"{symbol}: candle stream error — "
                f"{type(e).__name__}: {e} "
                f"— {len(rows)} bars collected"
            )

    return rows


async def fetch_intraday_bars(
    symbol:   str,
    interval: str = "1m",
) -> pd.DataFrame:
    """
    Fetch 1-min OHLCV bars from 09:30 ET to now using Tastytrade streamer.
    Returns DataFrame indexed by tz-aware datetime (ET).
    Returns empty DataFrame on failure.
    """
    start_time = _session_start_today()
    logger.info(
        f"{symbol}: fetching {interval} bars "
        f"from {start_time.strftime('%H:%M')} ET"
    )

    rows = await _stream_candles(symbol, interval, start_time)

    if not rows:
        logger.warning(
            f"{symbol}: no bars returned — "
            f"market may be closed or outside session hours"
        )
        return pd.DataFrame()

    df = pd.DataFrame(rows).set_index("datetime").sort_index()

    # Clip to regular session 09:30–16:00 ET
    today      = date.today()
    s_end      = datetime(today.year, today.month, today.day, 16, 0, tzinfo=ET)
    df         = df[df.index < s_end]

    if df.empty:
        logger.warning(f"{symbol}: no bars within regular session")
        return pd.DataFrame()

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
    Average daily volume over past N trading days via Tastytrade streamer.
    Cached per trading day — only fetched once per session.
    """
    today_str = date.today().strftime("%Y-%m-%d")

    if symbol in _avg_volume_cache:
        cached_date, cached_vol = _avg_volume_cache[symbol]
        if cached_date == today_str:
            return cached_vol

    # Fetch daily bars going back lookback*2 calendar days
    from_date  = datetime.now(ET) - timedelta(days=lookback * 2)
    rows       = await _stream_candles(symbol, "1d", from_date, max_bars=lookback * 2)

    if not rows:
        logger.warning(f"{symbol}: no daily volume data")
        _avg_volume_cache[symbol] = (today_str, 0.0)
        return 0.0

    volumes = [r["volume"] for r in rows if r["volume"] > 0]
    if not volumes:
        _avg_volume_cache[symbol] = (today_str, 0.0)
        return 0.0

    avg = float(sum(volumes[-lookback:]) / len(volumes[-lookback:]))
    _avg_volume_cache[symbol] = (today_str, avg)
    logger.info(f"{symbol}: avg daily volume = {avg:,.0f} (cached)")
    return avg
