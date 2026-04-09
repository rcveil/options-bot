"""
data/tastytrade.py
Tastytrade session, live greeks, quotes, option chain strikes, and IVR.

Authentication uses OAuth2 — only client_secret and refresh_token needed.
No username/password. No Session.create().

Required environment variables:
    TASTYTRADE_CLIENT_SECRET
    TASTYTRADE_REFRESH_TOKEN

All streamer calls have a 5-second timeout so they fail cleanly
outside market hours instead of hanging forever.
"""

import os
import asyncio
import logging
from datetime import date

from tastytrade import Session, DXLinkStreamer
from tastytrade.dxfeed import Greeks, Quote

logger = logging.getLogger(__name__)

# Module-level session cache — reused across all calls in the same process
_session: Session | None = None


async def get_session() -> Session:
    """
    Return a cached Tastytrade session.
    Creates a new session on first call, reuses it afterwards.

    Correct constructor for tastytrade==12.3.2:
        Session(client_secret, refresh_token)
    """
    global _session

    if _session is not None:
        return _session

    client_secret = os.getenv("TASTYTRADE_CLIENT_SECRET")
    refresh_token = os.getenv("TASTYTRADE_REFRESH_TOKEN")

    if not client_secret:
        raise ValueError(
            "TASTYTRADE_CLIENT_SECRET is not set. "
            "Add it to your .env file or Railway/Fly.io secrets."
        )
    if not refresh_token:
        raise ValueError(
            "TASTYTRADE_REFRESH_TOKEN is not set. "
            "Add it to your .env file or Railway/Fly.io secrets. "
            "Get one from: developer.tastytrade.com "
            "→ your app → Manage → Create Grant."
        )

    try:
        # Direct constructor — synchronous, no await needed
        _session = Session(client_secret, refresh_token)
        logger.info("Tastytrade session created successfully")
    except Exception as e:
        logger.error(f"Tastytrade session creation failed: {e}")
        _session = None
        raise

    return _session


async def get_quote(symbol: str) -> dict:
    """
    Fetch live bid/ask/mid for an underlying symbol.
    Times out after 5 seconds if no data (e.g. market closed).

    Returns dict with keys: bid, ask, mid
    """
    session = await get_session()

    logger.debug(f"Fetching quote for {symbol}")

    try:
        async with DXLinkStreamer(session) as streamer:
            await streamer.subscribe(Quote, [symbol])
            q = await asyncio.wait_for(
                streamer.get_event(Quote),
                timeout = 5.0,
            )
    except asyncio.TimeoutError:
        raise Exception(
            f"Quote timed out for {symbol} — market may be closed"
        )

    bid = float(q.bid_price) if q.bid_price else 0.0
    ask = float(q.ask_price) if q.ask_price else 0.0

    return {
        "bid": bid,
        "ask": ask,
        "mid": round((bid + ask) / 2, 2),
    }


async def get_greeks(
    symbol:      str,
    expiry:      str,   # "YYYY-MM-DD"
    strike:      float,
    option_type: str,   # "C" or "P"
) -> dict:
    """
    Fetch live greeks and bid/ask for a single option contract.
    Times out after 5 seconds if no data.

    Returns dict with keys:
        symbol, delta, gamma, theta, vega, rho, iv, bid, ask, mid
    """
    session = await get_session()

    # Build OCC-style dxfeed symbol e.g. .NVDA250418C870
    exp_fmt = expiry.replace("-", "")[2:]           # 250418
    occ     = f".{symbol}{exp_fmt}{option_type}{int(strike)}"

    logger.debug(f"Fetching greeks for {occ}")

    try:
        async with DXLinkStreamer(session) as streamer:
            await streamer.subscribe(Greeks, [occ])
            await streamer.subscribe(Quote,  [occ])
            g = await asyncio.wait_for(
                streamer.get_event(Greeks),
                timeout = 5.0,
            )
            q = await asyncio.wait_for(
                streamer.get_event(Quote),
                timeout = 5.0,
            )
    except asyncio.TimeoutError:
        raise Exception(
            f"Greeks timed out for {occ} — market may be closed"
        )

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
    """
    IV Rank (0-100) from Tastytrade market metrics endpoint.
    Falls back to 50.0 if unavailable or market is closed.
    """
    session = await get_session()
    try:
        from tastytrade.metrics import get_market_metrics
        metrics = await asyncio.wait_for(
            get_market_metrics(session, [symbol]),
            timeout = 5.0,
        )
        if metrics and metrics[0].implied_volatility_index_rank:
            ivr = float(metrics[0].implied_volatility_index_rank) * 100
            logger.debug(f"{symbol} IVR: {ivr:.1f}")
            return ivr
    except asyncio.TimeoutError:
        logger.warning(f"IVR fetch timed out for {symbol} — using default 50")
    except Exception as e:
        logger.warning(f"IVR fetch failed for {symbol}: {e} — using default 50")
    return 50.0


async def get_option_chain_strikes(
    symbol: str,
    expiry: str,   # "YYYY-MM-DD"
) -> list[dict]:
    """
    Return all available strikes for a given expiry date.

    Returns list of dicts sorted by strike:
        [{"strike": 870.0, "call_occ": ".NVDA...", "put_occ": "..."}, ...]

    Returns empty list if expiry not found or chain unavailable.
    """
    session = await get_session()

    try:
        from tastytrade.instruments import get_option_chain
        chain = await asyncio.wait_for(
            get_option_chain(session, symbol),
            timeout = 10.0,
        )
    except asyncio.TimeoutError:
        logger.error(f"Option chain timed out for {symbol}")
        return []
    except Exception as e:
        logger.error(f"Option chain fetch failed for {symbol}: {e}")
        return []

    target = date.fromisoformat(expiry)

    if target not in chain:
        logger.warning(
            f"{symbol}: expiry {expiry} not found in chain. "
            f"Available dates: {sorted(chain.keys())[:5]}"
        )
        return []

    strikes = []
    for instrument in chain[target]:
        try:
            strikes.append({
                "strike":   float(instrument.strike_price),
                "call_occ": instrument.call_streamer_symbol,
                "put_occ":  instrument.put_streamer_symbol,
            })
        except Exception as e:
            logger.debug(f"{symbol}: skipping instrument — {e}")
            continue

    result = sorted(strikes, key=lambda x: x["strike"])
    logger.debug(f"{symbol} {expiry}: {len(result)} strikes available")
    return result
