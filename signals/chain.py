"""
signals/chain.py
Option chain scanner: best expiry + best spread by delta and credit/width ratio.

Spread width is dynamic — not hardcoded.
Logic:
  1. Find the best short strike (delta 0.20–0.35 for credit, 0.40–0.55 for debit)
  2. Scan candidate long strikes at increasing distances from the short strike
  3. Select the narrowest width where credit >= 1/3 of width (33%)
  4. If no width meets the ratio, return None — filters.py will reject it anyway

This ensures the long leg is never too expensive relative to the premium collected,
and the spread width scales naturally with the underlying price and IV environment.
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

# Candidate wing widths to try, in order from narrow to wide
# Bot picks the narrowest width where credit/width >= 33%
CANDIDATE_WIDTHS = [2.5, 5, 7.5, 10, 12.5, 15, 20, 25]

# Minimum credit/width ratio — your 1/3 rule
MIN_CREDIT_RATIO = 0.33


async def select_expiry(
    symbol:  str,
    dte_min: int,
    dte_max: int,
) -> Optional[str]:
    """
    Return the nearest expiry (YYYY-MM-DD) within the DTE window.
    Returns None if no expiry found.
    """
    session = await get_session()
    try:
        from tastytrade.instruments import get_option_chain
        chain = await asyncio.wait_for(
            get_option_chain(session, symbol),
            timeout=10.0,
        )
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
            f"{symbol}: no expiry found in DTE {dte_min}–{dte_max}. "
            f"Available: {sorted(chain.keys())[:5]}"
        )
        return None

    candidates.sort(key=lambda x: x[0])
    chosen = candidates[0][1]
    logger.debug(f"{symbol}: selected expiry {chosen} ({candidates[0][0]} DTE)")
    return str(chosen)


async def _find_short_strike(
    symbol:           str,
    expiry:           str,
    option_type:      str,         # "P" or "C"
    structure:        str,         # "credit" or "debit"
    underlying_price: float,
) -> Optional[dict]:
    """
    Scan the chain for the best short (or long for debit) strike
    matching the target delta range.

    Returns dict with strike, greeks, or None if nothing found.
    """
    if structure == "credit":
        delta_min = DELTA_SHORT_CREDIT_MIN   # 0.20
        delta_max = DELTA_SHORT_CREDIT_MAX   # 0.35
    else:
        delta_min = DELTA_LONG_DEBIT_MIN     # 0.40
        delta_max = DELTA_LONG_DEBIT_MAX     # 0.55

    strikes_data = await get_option_chain_strikes(symbol, expiry)
    if not strikes_data:
        return None

    # Filter to OTM strikes only
    if option_type == "P":
        candidates = [
            s for s in strikes_data
            if s["strike"] < underlying_price * 0.998
        ]
        # Sort descending — nearest OTM first
        candidates = sorted(candidates,
                            key=lambda x: x["strike"], reverse=True)[:12]
    else:
        candidates = [
            s for s in strikes_data
            if s["strike"] > underlying_price * 1.002
        ]
        # Sort ascending — nearest OTM first
        candidates = sorted(candidates,
                            key=lambda x: x["strike"])[:12]

    best = None
    best_delta_diff = float("inf")
    target_delta = (delta_min + delta_max) / 2

    for strike_data in candidates:
        strike = strike_data["strike"]
        try:
            g = await get_greeks(symbol, expiry, strike, option_type)
        except Exception as e:
            logger.debug(f"{symbol} {strike}: greeks failed — {e}")
            continue

        abs_delta = abs(g["delta"])
        if not (delta_min <= abs_delta <= delta_max):
            continue
        if g["bid"] <= 0:
            continue

        delta_diff = abs(abs_delta - target_delta)
        if delta_diff < best_delta_diff:
            best_delta_diff = delta_diff
            best = {"strike": strike, "greeks": g}

    return best


async def _find_long_strike(
    symbol:       str,
    expiry:       str,
    option_type:  str,        # "P" or "C"
    short_strike: float,
    short_credit: float,      # mid price of short leg
    structure:    str,        # "credit" or "debit"
    strikes_data: list[dict],
) -> Optional[dict]:
    """
    Find the best long strike such that credit/width >= MIN_CREDIT_RATIO (33%).

    For credit spreads:
      - Puts: long strike is below short strike (buy cheaper put)
      - Calls: long strike is above short strike (buy cheaper call)
    For debit spreads:
      - Calls: long strike is the primary, short strike is above
      - Puts: long strike is the primary, short strike is above

    Tries candidate widths from narrow to wide.
    Returns the narrowest width that satisfies the ratio.
    """
    available_strikes = sorted(
        [s["strike"] for s in strikes_data]
    )

    best_result = None

    for width in CANDIDATE_WIDTHS:
        # Determine the long strike based on spread direction
        if option_type == "P":
            long_strike = short_strike - width    # put spread: buy lower put
        else:
            long_strike = short_strike + width    # call spread: buy higher call

        # Find the closest available strike to our target long strike
        closest = min(
            available_strikes,
            key=lambda x: abs(x - long_strike),
            default=None,
        )
        if closest is None:
            continue

        # Skip if the closest strike is the same as the short strike
        actual_width = abs(short_strike - closest)
        if actual_width < 1.0:
            continue

        # Fetch greeks for the long leg
        try:
            long_greeks = await get_greeks(
                symbol, expiry, closest, option_type
            )
        except Exception as e:
            logger.debug(f"{symbol} long leg {closest}: {e}")
            continue

        long_cost = long_greeks["mid"]
        net_credit = short_credit - long_cost

        if net_credit <= 0:
            continue

        ratio = net_credit / actual_width

        logger.debug(
            f"{symbol}: width={actual_width:.1f} "
            f"credit={net_credit:.2f} ratio={ratio:.0%}"
        )

        if ratio >= MIN_CREDIT_RATIO:
            # Found a valid width — return immediately (narrowest wins)
            return {
                "long_strike":   closest,
                "spread_width":  actual_width,
                "long_greeks":   long_greeks,
                "net_credit":    round(net_credit, 2),
                "credit_ratio":  round(ratio, 4),
            }

        # Keep track of best ratio found even if below threshold
        if best_result is None or ratio > best_result["credit_ratio"]:
            best_result = {
                "long_strike":   closest,
                "spread_width":  actual_width,
                "long_greeks":   long_greeks,
                "net_credit":    round(net_credit, 2),
                "credit_ratio":  round(ratio, 4),
            }

    # Nothing met the 33% rule — return best attempt anyway
    # filters.py will reject it via the credit/width gate
    if best_result:
        logger.warning(
            f"{symbol}: best ratio {best_result['credit_ratio']:.0%} "
            f"did not reach 33% threshold — filters.py will reject"
        )
    return best_result


async def find_best_spread(
    symbol:           str,
    expiry:           str,
    option_type:      str,
    structure:        str,
    direction:        str,
    underlying_price: float,
) -> Optional[dict]:
    """
    Full spread selection:
      1. Find best short strike by delta
      2. Find best long strike by credit/width ratio (>= 33%)

    Returns complete spread dict or None.
    """
    today    = date.today()
    dte      = (date.fromisoformat(expiry) - today).days

    # Step 1: Find short strike
    short = await _find_short_strike(
        symbol, expiry, option_type, structure, underlying_price
    )
    if short is None:
        logger.warning(f"{symbol}: no valid short strike found")
        return None

    short_strike = short["strike"]
    short_greeks = short["greeks"]
    short_credit = short_greeks["mid"]

    logger.info(
        f"{symbol}: short strike {short_strike} "
        f"delta={short_greeks['delta']:.3f} "
        f"credit=${short_credit:.2f}"
    )

    # Step 2: Get full strike list for long leg scanning
    strikes_data = await get_option_chain_strikes(symbol, expiry)
    if not strikes_data:
        return None

    # Step 3: Find long strike with best credit/width ratio
    long_result = await _find_long_strike(
        symbol        = symbol,
        expiry        = expiry,
        option_type   = option_type,
        short_strike  = short_strike,
        short_credit  = short_credit,
        structure     = structure,
        strikes_data  = strikes_data,
    )
    if long_result is None:
        logger.warning(f"{symbol}: no valid long strike found")
        return None

    long_strike  = long_result["long_strike"]
    spread_width = long_result["spread_width"]
    net_credit   = long_result["net_credit"]
    credit_ratio = long_result["credit_ratio"]

    # Buy strike vs sell strike
    if option_type == "P":
        sell_strike = short_strike
        buy_strike  = long_strike
    else:
        sell_strike = short_strike
        buy_strike  = long_strike

    max_loss = spread_width - net_credit

    logger.info(
        f"{symbol}: spread {sell_strike}/{buy_strike} "
        f"width=${spread_width:.1f} "
        f"credit=${net_credit:.2f} "
        f"ratio={credit_ratio:.0%} "
        f"max_loss=${max_loss:.2f}"
    )

    return {
        "sell_strike":   sell_strike,
        "buy_strike":    buy_strike,
        "spread_width":  spread_width,
        "short_delta":   short_greeks["delta"],
        "expiry":        expiry,
        "dte":           dte,
        "greeks":        short_greeks,
        "net_credit":    net_credit,
        "credit_ratio":  credit_ratio,
        "max_loss":      max_loss,
    }


async def build_spread(
    symbol:           str,
    structure:        str,
    direction:        str,
    underlying_price: float,
    dte_min:          int,
    dte_max:          int,
) -> Optional[dict]:
    """
    Entry point from main.py.
    Selects expiry then finds best spread.
    Returns None if no valid spread found.
    """
    # Determine option type from direction and structure
    if structure == "credit":
        option_type = "P" if direction == "bullish" else "C"
    else:
        option_type = "C" if direction == "bullish" else "P"

    # Select expiry
    expiry = await select_expiry(symbol, dte_min, dte_max)
    if not expiry:
        return None

    # Find best spread
    return await find_best_spread(
        symbol           = symbol,
        expiry           = expiry,
        option_type      = option_type,
        structure        = structure,
        direction        = direction,
        underlying_price = underlying_price,
    )
