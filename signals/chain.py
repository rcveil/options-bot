"""
signals/chain.py
Scans the option chain for the best strike matching target delta range.
Also selects the best expiry date matching target DTE range.

This replaces the hardcoded price * 0.97 placeholder in main.py.
"""

import asyncio
import logging
from datetime import date, timedelta
from typing import Optional

from data.tastytrade import get_session, get_greeks, get_option_chain_strikes
from signals.strategy import compute_pop
from config.thresholds import (
    DELTA_SHORT_CREDIT_MIN, DELTA_SHORT_CREDIT_MAX,
    DELTA_LONG_DEBIT_MIN,   DELTA_LONG_DEBIT_MAX,
    DTE_CREDIT_MIN, DTE_CREDIT_MAX,
    DTE_DEBIT_MIN,  DTE_DEBIT_MAX,
    MIN_OPEN_INTEREST, MIN_OPEN_INTEREST_SPX,
)

logger = logging.getLogger(__name__)


async def select_expiry(
    symbol:    str,
    dte_min:   int,
    dte_max:   int,
) -> Optional[str]:
    """
    Return the nearest expiry date (YYYY-MM-DD) within the DTE window.
    Prefers weeklies if available, falls back to monthly.
    """
    session = await get_session()
    try:
        from tastytrade.instruments import get_option_chain
        chain = await get_option_chain(session, symbol)
    except Exception as e:
        logger.error(f"Could not fetch option chain for {symbol}: {e}")
        return None

    today = date.today()
    candidates = []
    for exp_date in chain.keys():
        dte = (exp_date - today).days
        if dte_min <= dte <= dte_max:
            candidates.append((dte, exp_date))

    if not candidates:
        logger.warning(
            f"{symbol}: no expiry found in DTE {dte_min}–{dte_max}"
        )
        return None

    # Pick nearest expiry in range
    candidates.sort(key=lambda x: x[0])
    chosen = candidates[0][1]
    logger.debug(f"{symbol}: selected expiry {chosen} "
                 f"({candidates[0][0]} DTE)")
    return str(chosen)


async def find_best_strike(
    symbol:      str,
    expiry:      str,
    option_type: str,          # "P" or "C"
    structure:   str,          # "credit" or "debit"
    direction:   str,          # "bullish" or "bearish"
    underlying_price: float,
) -> Optional[dict]:
    """
    Scan strikes at the given expiry and return the one whose
    delta best fits the target range.

    For credit spreads: short leg delta 0.20–0.35 (OTM)
    For debit spreads:  long leg delta  0.40–0.55 (near ATM)

    Returns dict with:
        sell_strike, buy_strike, spread_width,
        short_delta, expiry, dte,
        greeks (for the short leg)
    """
    today = date.today()
    dte   = (date.fromisoformat(expiry) - today).days

    if structure == "credit":
        delta_min = DELTA_SHORT_CREDIT_MIN
        delta_max = DELTA_SHORT_CREDIT_MAX
        wing_width = 5.0    # default $5 wide spread
    else:
        delta_min = DELTA_LONG_DEBIT_MIN
        delta_max = DELTA_LONG_DEBIT_MAX
        wing_width = 5.0

    min_oi = MIN_OPEN_INTEREST_SPX \
             if symbol in ["SPX", "SPY", "QQQ"] \
             else MIN_OPEN_INTEREST

    # Fetch all strikes for this expiry
    strikes_data = await get_option_chain_strikes(symbol, expiry)
    if not strikes_data:
        logger.warning(f"{symbol} {expiry}: empty strike list")
        return None

    # Filter to OTM strikes only
    if option_type == "P":
        # Puts: only strikes below current price
        candidates = [
            s for s in strikes_data
            if s["strike"] < underlying_price * 0.995
        ]
    else:
        # Calls: only strikes above current price
        candidates = [
            s for s in strikes_data
            if s["strike"] > underlying_price * 1.005
        ]

    if not candidates:
        logger.warning(f"{symbol}: no OTM candidates found")
        return None

    # Fetch greeks concurrently for candidate strikes
    # Limit to 10 nearest OTM strikes to avoid rate limits
    if option_type == "P":
        candidates = sorted(candidates,
                            key=lambda x: x["strike"],
                            reverse=True)[:10]
    else:
        candidates = sorted(candidates,
                            key=lambda x: x["strike"])[:10]

    best = None
    best_delta_diff = float("inf")

    for strike_data in candidates:
        strike = strike_data["strike"]
        try:
            g = await get_greeks(symbol, expiry, strike, option_type)
        except Exception as e:
            logger.debug(f"{symbol} {strike}: greeks fetch failed — {e}")
            continue

        abs_delta = abs(g["delta"])

        # Check delta fits our range
        if not (delta_min <= abs_delta <= delta_max):
            continue

        # Check bid/ask is not zero (illiquid)
        if g["bid"] <= 0:
            continue

        # Pick strike closest to centre of delta range
        target_delta = (delta_min + delta_max) / 2
        delta_diff   = abs(abs_delta - target_delta)

        if delta_diff < best_delta_diff:
            best_delta_diff = delta_diff
            best = {
                "sell_strike":   strike,
                "buy_strike":    strike - wing_width
                                 if option_type == "P"
                                 else strike + wing_width,
                "spread_width":  wing_width,
                "short_delta":   g["delta"],
                "expiry":        expiry,
                "dte":           dte,
                "greeks":        g,
            }

    if best:
        logger.info(
            f"{symbol}: best strike {best['sell_strike']} "
            f"delta={best['short_delta']:.3f} expiry={expiry}"
        )
    else:
        logger.warning(
            f"{symbol}: no strike found in delta range "
            f"{delta_min}–{delta_max} for {expiry}"
        )

    return best


async def build_spread(
    symbol:           str,
    structure:        str,   # "credit" or "debit"
    direction:        str,   # "bullish" / "bearish"
    underlying_price: float,
    dte_min:          int,
    dte_max:          int,
) -> Optional[dict]:
    """
    End-to-end: pick expiry → find best strike → return spread dict.

    Returns None if no valid spread found.
    The returned dict is ready to be passed into filters.py and
    formatter.py.
    """
    # 1. Pick option type from direction + structure
    if structure == "credit":
        # Bull put spread → sell puts; Bear call spread → sell calls
        option_type = "P" if direction == "bullish" else "C"
    else:
        # Bull call spread → buy calls; Bear put spread → buy puts
        option_type = "C" if direction == "bullish" else "P"

    # 2. Select expiry
    expiry = await select_expiry(symbol, dte_min, dte_max)
    if not expiry:
        return None

    # 3. Find best strike
    spread = await find_best_strike(
        symbol           = symbol,
        expiry           = expiry,
        option_type      = option_type,
        structure        = structure,
        direction        = direction,
        underlying_price = underlying_price,
    )

    return spread
