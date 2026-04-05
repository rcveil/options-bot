import logging
from tastytrade import DXLinkStreamer
from tastytrade.dxfeed import Quote
from data.tastytrade import get_session
from config.thresholds import VIX_NORMAL, VIX_ELEVATED, VIX_SPIKE, VIX_PAUSE

logger = logging.getLogger(__name__)


async def get_vix() -> float:
    """Fetch live VIX mid price."""
    session = await get_session()
    async with DXLinkStreamer(session) as streamer:
        await streamer.subscribe(Quote, ["VIX"])
        q = await streamer.get_event(Quote)
    return round((float(q.bid_price) + float(q.ask_price)) / 2, 2)


def classify_vix(vix: float) -> str:
    """Return regime string based on VIX level."""
    if vix >= VIX_PAUSE:    return "pause"
    if vix >= VIX_SPIKE:    return "spike"
    if vix >= VIX_ELEVATED: return "elevated"
    return "normal"


async def get_ivr(symbol: str) -> float:
    """
    IV Rank from Tastytrade market metrics endpoint.
    Falls back to 50 if unavailable.
    """
    session = await get_session()
    try:
        from tastytrade.metrics import get_market_metrics
        metrics = await get_market_metrics(session, [symbol])
        if metrics and metrics[0].implied_volatility_index_rank:
            return float(metrics[0].implied_volatility_index_rank) * 100
    except Exception as e:
        logger.warning(f"IVR fetch failed for {symbol}: {e}")
    return 50.0
