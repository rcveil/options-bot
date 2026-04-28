"""
data/candles.py
Fetches 1-minute OHLCV bars using Tastytrade DXLinkStreamer.

Key change from v3: use get_event() in a loop instead of listen().
listen() is an infinite generator that blocks on a queue — if the queue
never populates (candle subscription fails silently), listen() hangs forever.

get_event() with a short timeout per event allows us to collect whatever
arrives and exit gracefully after a reasonable wait.

Avg volume cached per trading day.
"""

import asyncio
import logging
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from data.tastytrade import get_session

logger = logging.getLogger(__name__)
ET     = ZoneInfo("America/New_York")

EVENT_TIMEOUT = 2.0    # seconds to wait per individual event
MAX_EVENTS    = 100    # max bars to collect
TOTAL_TIMEOUT = 15.0   # total time budget for the entire fetch

# Avg volume cache: {symbol: (date_str, avg_volume)}
_avg_volume_cache: dict = {}


def _session_start_today() -> datetime:
    today = date.today()
    return datetime(today.year, today.month, today.day, 9, 30, tzinfo=ET)


async def _stream_candles(
    symbol:     str,
    interval:   str,
    start_time: datetime,
    max_events: int = MAX_EVENTS,
) -> list[dict]:
    """
    Subscribe to candle stream and collect bars using get_event() loop.
    Returns list of bar dicts.
    """
    from tastytrade import DXLinkStreamer
    from tastytrade.dxfeed import Candle

    session = await get_session()
    rows    = []

    try:
        async with asyncio.timeout(TOTAL_TIMEOUT):
            async with DXLinkStreamer(session) as streamer:
                await streamer.subscribe_candle(
                    symbols                = [symbol],
                    interval               = interval,
                    start_time             = start_time,
                    extended_trading_hours = False,
                )

                # Collect events one at a time with individual timeout
                for _ in range(max_events):
                    try:
                        candle = await asyncio.wait_for(
                            streamer.get_event(Candle),
                            timeout=EVENT_TIMEOUT,
                        )

                        bar_time = datetime.fromtimestamp(
                            candle.time / 1000, tz=ET
                        )

                        if bar_time < start_time:
                            continue

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

                    except asyncio.TimeoutError:
                        # No more events arriving — exit cleanly
                        logger.debug(
                            f"{symbol}: no event within {EVENT_TIMEOUT}s "
                            f"— ending stream with {len(rows)} bars"
                        )
                        break

    except asyncio.TimeoutError:
        logger.warning(
            f"{symbol}: total timeout {TOTAL_TIMEOUT}s "
            f"— returning {len(rows)} bars"
        )
    except BaseException as e:
        if type(e).__name__ in ("ExceptionGroup", "BaseExceptionGroup"):
            logger.warning(
                f"{symbol}: TaskGroup exception "
                f"— {len(rows)} bars collected"
            )
        else:
            logger.warning(
                f"{symbol}: stream error {type(e).__name__}: {e} "
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
            f"market may be closed or symbol unavailable"
        )
        return pd.DataFrame()

    df = pd.DataFrame(rows).set_index("datetime").sort_index()

    # Clip to regular session 09:30–16:00 ET
    today = date.today()
    s_end = datetime(today.year, today.month, today.day, 16, 0, tzinfo=ET)
    df    = df[df.index < s_end]

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
    Average daily volume over past N trading days.
    Cached per trading day — only fetched once per session.
    """
    today_str = date.today().strftime("%Y-%m-%d")

    if symbol in _avg_volume_cache:
        cached_date, cached_vol = _avg_volume_cache[symbol]
        if cached_date == today_str:
            return cached_vol

    # Fetch daily bars
    from_date = datetime.now(ET) - timedelta(days=lookback * 2)
    rows      = await _stream_candles(
        symbol, "1d", from_date, max_events=lookback * 2
    )

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
