"""
data/market.py
VIX fetch, IVR fetch, VIX regime classifier.

VIX strategy (in order):
1. Try Yahoo Finance public API (no auth, reliable during market hours)
2. Try Tastytrade DXLink streamer with multiple symbol formats
3. Fall back to 20.0 (elevated-normal boundary) with a warning
"""

import asyncio
import logging

import aiohttp

from data.tastytrade import get_session
from config.thresholds import VIX_NORMAL, VIX_ELEVATED, VIX_SPIKE, VIX_PAUSE

logger = logging.getLogger(__name__)

VIX_SYMBOLS    = ["$VIX.X", "VIX", "CBOE:VIX"]
VIX_YAHOO_URL  = "https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX?interval=1m&range=1d"
VIX_DEFAULT    = 20.0   # safe fallback — sits at elevated/normal boundary


async def _fetch_vix_yahoo() -> float | None:
    """Fetch VIX from Yahoo Finance public API. Returns None on failure."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                VIX_YAHOO_URL,
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                price = (
                    data["chart"]["result"][0]
                    ["meta"]["regularMarketPrice"]
                )
                return round(float(price), 2)
    except Exception as e:
        logger.warning(f"Yahoo VIX fetch failed: {e}")
        return None


async def _fetch_vix_tastytrade() -> float | None:
    """Fetch VIX from Tastytrade DXLink streamer. Returns None on failure."""
    from tastytrade import DXLinkStreamer
    from tastytrade.dxfeed import Quote

    session = await get_session()

    for symbol in VIX_SYMBOLS:
        try:
            async with DXLinkStreamer(session) as streamer:
                await streamer.subscribe(Quote, [symbol])
                q = await asyncio.wait_for(
                    streamer.get_event(Quote),
                    timeout=5.0,
                )
            bid = float(q.bid_price) if q.bid_price else 0.0
            ask = float(q.ask_price) if q.ask_price else 0.0
            mid = round((bid + ask) / 2, 2)
            if mid > 0:
                logger.info(f"VIX fetched via Tastytrade {symbol}: {mid}")
                return mid
        except asyncio.TimeoutError:
            logger.warning(f"Tastytrade VIX timeout with {symbol}")
            continue
        except Exception as e:
            logger.warning(f"Tastytrade VIX failed with {symbol}: {e}")
            continue

    return None


async def get_vix() -> float:
    """
    Fetch live VIX. Tries Yahoo Finance first, then Tastytrade,
    then returns a safe default of 20.0 with a warning.
    """
    # 1. Try Yahoo Finance
    vix = await _fetch_vix_yahoo()
    if vix and vix > 0:
        logger.info(f"VIX from Yahoo Finance: {vix}")
        return vix

    # 2. Try Tastytrade streamer
    vix = await _fetch_vix_tastytrade()
    if vix and vix > 0:
        return vix

    # 3. Safe default
    logger.warning(
        f"VIX unavailable from all sources. "
        f"Using default {VIX_DEFAULT} (elevated/normal boundary). "
        f"Signals will fire with reduced size as precaution."
    )
    return VIX_DEFAULT


def classify_vix(vix: float) -> str:
    if vix >= VIX_PAUSE:    return "pause"
    if vix >= VIX_SPIKE:    return "spike"
    if vix >= VIX_ELEVATED: return "elevated"
    return "normal"


async def get_ivr(symbol: str) -> float:
    """
    IV Rank (0-100) from Tastytrade market metrics.
    Falls back to 50.0 if unavailable.
    """
    session = await get_session()
    try:
        from tastytrade.metrics import get_market_metrics
        metrics = await asyncio.wait_for(
            get_market_metrics(session, [symbol]),
            timeout=5.0,
        )
        if metrics and metrics[0].implied_volatility_index_rank:
            return float(metrics[0].implied_volatility_index_rank) * 100
    except asyncio.TimeoutError:
        logger.warning(f"IVR timeout for {symbol}, using default 50")
    except Exception as e:
        logger.warning(f"IVR failed for {symbol}: {e}, using default 50")
    return 50.0
