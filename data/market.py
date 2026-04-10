"""
data/market.py
VIX fetch, IVR fetch, VIX regime classifier.
"""

import asyncio
import logging

from tastytrade import DXLinkStreamer
from tastytrade.dxfeed import Quote

from data.tastytrade import get_session
from config.thresholds import VIX_NORMAL, VIX_ELEVATED, VIX_SPIKE, VIX_PAUSE

logger = logging.getLogger(__name__)

# Tastytrade uses $VIX.X for spot VIX — fallbacks included
VIX_SYMBOLS = ["$VIX.X", "VIX", "CBOE:VIX"]


async def get_vix() -> float:
    """
    Fetch live VIX mid price.
    Tries multiple symbol formats until one returns data.
    Raises if all fail.
    """
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
                logger.info(f"VIX fetched via {symbol}: {mid}")
                return mid
        except asyncio.TimeoutError:
            logger.warning(f"VIX timeout with {symbol}, trying next")
            continue
        except Exception as e:
            logger.warning(f"VIX failed with {symbol}: {e}")
            continue

    raise Exception("VIX unavailable — all symbol formats failed")


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
