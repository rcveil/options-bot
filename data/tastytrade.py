import asyncio
import logging
from tastytrade import Session, DXLinkStreamer
from tastytrade.dxfeed import Greeks, Quote
from config.settings import TASTYTRADE_USERNAME, TASTYTRADE_PASSWORD

logger = logging.getLogger(__name__)

_session: Session | None = None


async def get_session() -> Session:
    global _session
    if _session is None:
        _session = await Session.create(
            TASTYTRADE_USERNAME,
            TASTYTRADE_PASSWORD,
        )
        logger.info("Tastytrade session created")
    return _session


async def get_greeks(
    symbol:      str,
    expiry:      str,   # "YYYY-MM-DD"
    strike:      float,
    option_type: str,   # "C" or "P"
) -> dict:
    """Fetch live greeks + quote for a single option contract."""
    session = await get_session()

    # Build OCC symbol: .NVDA250418C870
    exp_fmt = expiry.replace("-", "")[2:]          # 250418
    occ     = f".{symbol}{exp_fmt}{option_type}{int(strike)}"

    async with DXLinkStreamer(session) as streamer:
        await streamer.subscribe(Greeks, [occ])
        await streamer.subscribe(Quote,  [occ])
        g = await streamer.get_event(Greeks)
        q = await streamer.get_event(Quote)

    bid = float(q.bid_price)
    ask = float(q.ask_price)
    return {
        "symbol":    occ,
        "delta":     round(g.delta,      4),
        "gamma":     round(g.gamma,      4),
        "theta":     round(g.theta,      4),
        "vega":      round(g.vega,       4),
        "rho":       round(g.rho,        4),
        "iv":        round(g.volatility, 4),
        "bid":       bid,
        "ask":       ask,
        "mid":       round((bid + ask) / 2, 2),
    }


async def get_quote(symbol: str) -> dict:
    """Fetch live bid/ask/mid for an underlying."""
    session = await get_session()
    async with DXLinkStreamer(session) as streamer:
        await streamer.subscribe(Quote, [symbol])
        q = await streamer.get_event(Quote)
    bid = float(q.bid_price)
    ask = float(q.ask_price)
    return {
        "bid": bid,
        "ask": ask,
        "mid": round((bid + ask) / 2, 2),
    }


async def get_option_chain_strikes(
    symbol: str,
    expiry: str,
) -> list[dict]:
    """
    Return all strikes for a given expiry as a list of dicts:
    [{"strike": 870.0, "call_occ": ".NVDA...", "put_occ": "..."}, ...]
    """
    session = await get_session()
    from tastytrade.instruments import get_option_chain
    chain = await get_option_chain(session, symbol)

    from datetime import date
    target = date.fromisoformat(expiry)
    strikes = []
    for instrument in chain.get(target, []):
        strikes.append({
            "strike":    float(instrument.strike_price),
            "call_occ":  instrument.call_streamer_symbol,
            "put_occ":   instrument.put_streamer_symbol,
        })
    return sorted(strikes, key=lambda x: x["strike"])
