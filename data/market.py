"""
data/market.py
VIX fetch, IVR fetch, VIX regime classifier.
All streamer calls have a 5-second timeout so they fail
cleanly outside market hours instead of hanging forever.
"""

import asyncio
import logging

from tastytrade import DXLinkStreamer
from tastytrade.dxfeed import Quote

from data.tastytrade import get_session
from config.thresholds import VIX_NORMAL, VIX_ELEVATED, VIX_SPIKE, VIX_PAUSE

logger = logging.getLogger(__name__)


async def get_vix() -> float:
    """
    Fetch live VIX mid price.
    Raises exception with clear message if market is closed or data unavailable.
    """
    session = await get_session()
    try:
        async with DXLinkStreamer(session) as streamer:
            await streamer.subscribe(Quote, ["VIX"])
            q = await asyncio.wait_for(
                streamer.get_event(Quote),
                timeout = 5.0,
            )
        bid = float(q.bid_price) if q.bid_price else 0.0
        ask = float(q.ask_price) if q.ask_price else 0.0
        return round((bid + ask) / 2, 2)
    except asyncio.TimeoutError:
        raise Exception("VIX quote timed out — market may be closed")
    except Exception as e:
        logger.warning(f"VIX fetch failed: {e}")
        raise


def classify_vix(vix: float) -> str:
    """Return regime string based on VIX level."""
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
            timeout = 5.0,
        )
        if metrics and metrics[0].implied_volatility_index_rank:
            return float(metrics[0].implied_volatility_index_rank) * 100
    except asyncio.TimeoutError:
        logger.warning(f"IVR fetch timed out for {symbol}")
    except Exception as e:
        logger.warning(f"IVR fetch failed for {symbol}: {e}")
    return 50.0