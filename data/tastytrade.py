"""
data/tastytrade.py
Tastytrade session, live greeks, quotes, option chain strikes, and IVR.

Key fix: get_option_chain_strikes() now accepts an optional pre-fetched
chain dict to avoid double API calls that caused empty strike lists.
"""

import os
import asyncio
import logging
from datetime import date

from tastytrade import Session, DXLinkStreamer
from tastytrade.dxfeed import Greeks, Quote

logger = logging.getLogger(__name__)

_session: Session | None = None


async def get_session() -> Session:
    global _session
    if _session is not None:
        return _session

    client_secret = os.getenv("TASTYTRADE_CLIENT_SECRET")
    refresh_token = os.getenv("TASTYTRADE_REFRESH_TOKEN")

    if not client_secret:
        raise ValueError("TASTYTRADE_CLIENT_SECRET is not set.")
    if not refresh_token:
        raise ValueError("TASTYTRADE_REFRESH_TOKEN is not set.")

    try:
        _session = Session(client_secret, refresh_token)
        logger.info("Tastytrade session created successfully")
    except Exception as e:
        logger.error(f"Tastytrade session creation failed: {e}")
        _session = None
        raise

    return _session


async def get_quote(symbol: str) -> dict:
    session = await get_session()
    try:
        async with DXLinkStreamer(session) as streamer:
            await streamer.subscribe(Quote, [symbol])
            q = await asyncio.wait_for(
                streamer.get_event(Quote), timeout=5.0
            )
        bid = float(q.bid_price) if q.bid_price else 0.0
        ask = float(q.ask_price) if q.ask_price else 0.0
        return {"bid": bid, "ask": ask, "mid": round((bid + ask) / 2, 2)}
    except asyncio.TimeoutError:
        raise Exception(f"Quote timed out for {symbol}")


async def get_greeks(
    symbol:      str,
    expiry:      str,
    strike:      float,
    option_type: str,
) -> dict:
    session = await get_session()
    exp_fmt = expiry.replace("-", "")[2:]
    occ     = f".{symbol}{exp_fmt}{option_type}{int(strike)}"

    try:
        async with DXLinkStreamer(session) as streamer:
            await streamer.subscribe(Greeks, [occ])
            await streamer.subscribe(Quote,  [occ])
            g = await asyncio.wait_for(streamer.get_event(Greeks), timeout=5.0)
            q = await asyncio.wait_for(streamer.get_event(Quote),  timeout=5.0)
    except asyncio.TimeoutError:
        raise Exception(f"Greeks timed out for {occ}")

    bid = float(q.bid_price) if q.bid_price else 0.0
    ask = float(q.ask_price) if q.ask_price else 0.0

    return {
        "symbol": occ,
        "delta":  round(float(g.delta),      4) if g.delta      else 0.0,
        "gamma":  round(float(g.gamma),      4) if g.gamma      else 0.0,
        "theta":  round(float(g.theta),      4) if g.theta      else 0.0,
        "vega":   round(float(g.vega),       4) if g.vega       else 0.0,
        "rho":    round(float(g.rho),        4) if g.rho        else 0.0,
        "iv":     round(float(g.volatility), 4) if g.volatility else 0.0,
        "bid":    bid,
        "ask":    ask,
        "mid":    round((bid + ask) / 2, 2),
    }


async def get_ivr(symbol: str) -> float:
    session = await get_session()
    try:
        from tastytrade.metrics import get_market_metrics
        metrics = await asyncio.wait_for(
            get_market_metrics(session, [symbol]), timeout=5.0
        )
        if metrics and metrics[0].implied_volatility_index_rank:
            return float(metrics[0].implied_volatility_index_rank) * 100
    except asyncio.TimeoutError:
        logger.warning(f"IVR timeout for {symbol}, using default 50")
    except Exception as e:
        logger.warning(f"IVR failed for {symbol}: {e}, using default 50")
    return 50.0


async def fetch_option_chain(symbol: str) -> dict:
    """
    Fetch the full option chain dict for a symbol.
    Returns {date: [instruments]} or empty dict on failure.
    This is the single source of truth — call once and pass through.
    """
    session = await get_session()
    try:
        from tastytrade.instruments import get_option_chain
        chain = await asyncio.wait_for(
            get_option_chain(session, symbol), timeout=10.0
        )
        dates = sorted(chain.keys())
        logger.info(
            f"{symbol}: chain fetched — "
            f"{len(chain)} expiry dates: "
            f"{[str(d) for d in dates[:10]]}"
        )
        return chain
    except asyncio.TimeoutError:
        logger.error(f"{symbol}: option chain fetch timed out")
        return {}
    except Exception as e:
        logger.error(f"{symbol}: option chain fetch failed — {e}")
        return {}


def get_strikes_for_expiry(
    chain:  dict,
    expiry: str,
    symbol: str = "?",
) -> list[dict]:
    """
    Extract strikes from a pre-fetched chain dict for a given expiry.
    Returns sorted list of strike dicts or empty list.

    This avoids the double API call bug where two separate fetch_option_chain()
    calls returned different chain structures.
    """
    target = date.fromisoformat(expiry)

    # Try exact match first
    if target in chain:
        instruments = chain[target]
    else:
        # Fallback: match by string comparison in case keys are different types
        matched = None
        for k in chain.keys():
            if str(k) == expiry or (hasattr(k, 'isoformat') and k.isoformat() == expiry):
                matched = k
                break
        if matched is None:
            logger.warning(
                f"{symbol}: expiry {expiry} not found in chain. "
                f"Available: {sorted(str(k) for k in chain.keys())[:8]}"
            )
            return []
        instruments = chain[matched]

    if not instruments:
        logger.warning(f"{symbol}: expiry {expiry} found but has 0 instruments")
        return []

    strikes = []
    for inst in instruments:
        try:
            strikes.append({
                "strike":   float(inst.strike_price),
                "call_occ": inst.call_streamer_symbol,
                "put_occ":  inst.put_streamer_symbol,
            })
        except Exception as e:
            logger.debug(f"{symbol}: skipping instrument — {e}")
            continue

    result = sorted(strikes, key=lambda x: x["strike"])
    logger.info(f"{symbol} {expiry}: {len(result)} strikes available")
    return result
