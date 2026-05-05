"""
signals/chain.py
Option chain scanner: best expiry, best spread, iron condor, butterfly.

Butterfly structure:
- Buy 1 lower strike (ITM call)
- Sell 2 middle strikes (ATM call)
- Buy 1 upper strike (OTM call)
- Equal wing spacing ($5-$10)
- Net debit trade
"""

import asyncio
import logging
from datetime import date
from typing import Optional

from data.tastytrade import (
    get_greeks, fetch_option_chain, get_strikes_for_expiry
)
from config.thresholds import (
    DELTA_SHORT_CREDIT_MIN, DELTA_SHORT_CREDIT_MAX,
    DELTA_LONG_DEBIT_MIN,   DELTA_LONG_DEBIT_MAX,
    DTE_CREDIT_MIN, DTE_CREDIT_MAX,
    DTE_DEBIT_MIN,  DTE_DEBIT_MAX,
)

logger = logging.getLogger(__name__)

CANDIDATE_WIDTHS = [2.5, 5, 7.5, 10, 12.5, 15, 20, 25]
MIN_CREDIT_RATIO = 0.33
BUTTERFLY_WING_WIDTHS = [5, 7.5, 10]  # Per-wing spacing for butterflies


def _select_expiry_from_chain(
    chain: dict, symbol: str, dte_min: int, dte_max: int
) -> Optional[str]:
    today      = date.today()
    candidates = [
        ((exp - today).days, exp)
        for exp in chain.keys()
        if dte_min <= (exp - today).days <= dte_max
    ]
    if not candidates:
        all_dtes = sorted((exp - today).days for exp in chain.keys())
        logger.warning(
            f"{symbol}: no expiry in DTE {dte_min}–{dte_max}. "
            f"Available DTEs: {all_dtes[:10]}"
        )
        return None
    candidates.sort(key=lambda x: x[0])
    chosen = candidates[0][1]
    logger.info(f"{symbol}: selected expiry {chosen} ({candidates[0][0]} DTE)")
    return str(chosen)


async def select_expiry(
    symbol: str, dte_min: int, dte_max: int
) -> Optional[str]:
    chain = await fetch_option_chain(symbol)
    if not chain:
        return None
    return _select_expiry_from_chain(chain, symbol, dte_min, dte_max)


async def _find_short_strike(
    symbol:           str,
    expiry:           str,
    option_type:      str,
    structure:        str,
    underlying_price: float,
    strikes_data:     list[dict],
) -> Optional[dict]:
    """Find best short strike by delta. strikes_data has {"strike","C","P"} dicts."""
    delta_min = DELTA_SHORT_CREDIT_MIN if structure == "credit" else DELTA_LONG_DEBIT_MIN
    delta_max = DELTA_SHORT_CREDIT_MAX if structure == "credit" else DELTA_LONG_DEBIT_MAX

    if option_type == "P":
        candidates = sorted(
            [s for s in strikes_data if s["strike"] < underlying_price * 0.998],
            key=lambda x: x["strike"], reverse=True
        )[:12]
    else:
        candidates = sorted(
            [s for s in strikes_data if s["strike"] > underlying_price * 1.002],
            key=lambda x: x["strike"]
        )[:12]

    logger.info(
        f"{symbol}: scanning {len(candidates)} {option_type} strikes "
        f"for delta {delta_min}–{delta_max} (underlying={underlying_price:.2f})"
    )

    best            = None
    best_delta_diff = float("inf")
    target_delta    = (delta_min + delta_max) / 2

    for sd in candidates:
        try:
            g = await get_greeks(symbol, expiry, sd["strike"], option_type)
        except Exception as e:
            logger.warning(f"{symbol} strike {sd['strike']}: greeks failed — {e}")
            continue
        abs_delta = abs(g["delta"])
        if not (delta_min <= abs_delta <= delta_max):
            continue
        if g["bid"] <= 0:
            continue
        diff = abs(abs_delta - target_delta)
        if diff < best_delta_diff:
            best_delta_diff = diff
            best = {"strike": sd["strike"], "greeks": g}

    if best:
        logger.info(
            f"{symbol}: short strike {best['strike']} "
            f"delta={best['greeks']['delta']:.3f} mid=${best['greeks']['mid']:.2f}"
        )
    else:
        logger.warning(f"{symbol}: no {option_type} strike found in delta range")
    return best


async def _find_long_strike(
    symbol:       str,
    expiry:       str,
    option_type:  str,
    short_strike: float,
    short_credit: float,
    strikes_data: list[dict],
) -> Optional[dict]:
    """Find narrowest long strike where net_credit >= 33% of width."""
    available   = sorted([s["strike"] for s in strikes_data])
    best_result = None

    for width in CANDIDATE_WIDTHS:
        target_long = (short_strike - width) if option_type == "P" \
                      else (short_strike + width)
        closest = min(available, key=lambda x: abs(x - target_long), default=None)
        if closest is None:
            continue
        actual_width = abs(short_strike - closest)
        if actual_width < 1.0:
            continue

        try:
            long_g = await get_greeks(symbol, expiry, closest, option_type)
        except Exception:
            continue

        net_credit = short_credit - long_g["mid"]
        if net_credit <= 0:
            continue

        ratio = net_credit / actual_width
        logger.info(
            f"{symbol}: width=${actual_width:.1f} short={short_strike} "
            f"long={closest} credit=${net_credit:.2f} ratio={ratio:.0%} "
            f"{'✓' if ratio >= MIN_CREDIT_RATIO else '✗'}"
        )

        result = {
            "long_strike":  closest,
            "spread_width": actual_width,
            "net_credit":   round(net_credit, 2),
            "credit_ratio": round(ratio, 4),
        }

        if ratio >= MIN_CREDIT_RATIO:
            return result
        if best_result is None or ratio > best_result["credit_ratio"]:
            best_result = result

    if best_result:
        logger.warning(
            f"{symbol}: best ratio {best_result['credit_ratio']:.0%} "
            f"below 33% — filters.py will reject"
        )
    return best_result


async def _build_one_wing(
    symbol:           str,
    expiry:           str,
    option_type:      str,
    underlying_price: float,
    strikes_data:     list[dict],
) -> Optional[dict]:
    logger.info(f"{symbol}: building {option_type} wing for {expiry}")
    short = await _find_short_strike(
        symbol, expiry, option_type, "credit",
        underlying_price, strikes_data,
    )
    if short is None:
        return None

    long_result = await _find_long_strike(
        symbol, expiry, option_type,
        short["strike"], short["greeks"]["mid"], strikes_data,
    )
    if long_result is None:
        return None

    today = date.today()
    dte   = (date.fromisoformat(expiry) - today).days

    return {
        "sell_strike":   short["strike"],
        "buy_strike":    long_result["long_strike"],
        "spread_width":  long_result["spread_width"],
        "net_credit":    long_result["net_credit"],
        "credit_ratio":  long_result["credit_ratio"],
        "max_loss":      round(long_result["spread_width"] - long_result["net_credit"], 2),
        "greeks":        short["greeks"],
        "expiry":        expiry,
        "dte":           dte,
        "option_type":   option_type,
    }


async def find_best_spread(
    symbol:           str,
    expiry:           str,
    option_type:      str,
    structure:        str,
    direction:        str,
    underlying_price: float,
    strikes_data:     list[dict],
) -> Optional[dict]:
    logger.info(
        f"{symbol}: finding {structure} {option_type} spread "
        f"for {expiry} (direction={direction})"
    )
    today = date.today()
    dte   = (date.fromisoformat(expiry) - today).days

    short = await _find_short_strike(
        symbol, expiry, option_type, structure,
        underlying_price, strikes_data,
    )
    if short is None:
        return None

    long_result = await _find_long_strike(
        symbol, expiry, option_type,
        short["strike"], short["greeks"]["mid"], strikes_data,
    )
    if long_result is None:
        return None

    spread_width = long_result["spread_width"]
    net_credit   = long_result["net_credit"]
    credit_ratio = long_result["credit_ratio"]
    max_loss     = round(spread_width - net_credit, 2)

    logger.info(
        f"{symbol}: spread {short['strike']}/{long_result['long_strike']} "
        f"width=${spread_width:.1f} credit=${net_credit:.2f} "
        f"ratio={credit_ratio:.0%} max_loss=${max_loss:.2f}"
    )

    return {
        "sell_strike":   short["strike"],
        "buy_strike":    long_result["long_strike"],
        "spread_width":  spread_width,
        "net_credit":    net_credit,
        "credit_ratio":  credit_ratio,
        "max_loss":      max_loss,
        "greeks":        short["greeks"],
        "expiry":        expiry,
        "dte":           dte,
    }


async def build_butterfly(
    symbol:           str,
    underlying_price: float,
    dte_min:          int = 25,
    dte_max:          int = 35,
) -> Optional[dict]:
    """
    Build long call butterfly:
    - Buy 1 lower strike (ITM)
    - Sell 2 middle strikes (ATM)
    - Buy 1 upper strike (OTM)
    
    Equal wing spacing. Tries $5, $7.5, $10 widths.
    Returns first structure where debit/width <= 25%.
    """
    logger.info(f"{symbol}: building butterfly (underlying={underlying_price:.2f})")

    chain = await fetch_option_chain(symbol)
    if not chain:
        return None

    expiry = _select_expiry_from_chain(chain, symbol, dte_min, dte_max)
    if not expiry:
        return None

    strikes_data = get_strikes_for_expiry(chain, expiry, symbol)
    if not strikes_data:
        logger.warning(f"{symbol} butterfly: no strikes for {expiry}")
        return None

    available_strikes = sorted([s["strike"] for s in strikes_data])
    
    # Find ATM strike (middle/body)
    atm_strike = min(available_strikes, key=lambda x: abs(x - underlying_price))
    
    today = date.today()
    dte   = (date.fromisoformat(expiry) - today).days

    # Try each wing width
    for wing_width in BUTTERFLY_WING_WIDTHS:
        lower_target = atm_strike - wing_width
        upper_target = atm_strike + wing_width
        
        # Find closest actual strikes
        lower_strike = min(available_strikes, key=lambda x: abs(x - lower_target), default=None)
        upper_strike = min(available_strikes, key=lambda x: abs(x - upper_target), default=None)
        
        if lower_strike is None or upper_strike is None:
            continue
        
        actual_lower_width = atm_strike - lower_strike
        actual_upper_width = upper_strike - atm_strike
        
        # Wings should be roughly equal
        if abs(actual_lower_width - actual_upper_width) > 1.0:
            logger.info(
                f"{symbol} butterfly: wings unequal "
                f"(lower={actual_lower_width:.1f} upper={actual_upper_width:.1f}) — skipping"
            )
            continue
        
        # Fetch greeks for all 3 strikes
        try:
            lower_g = await get_greeks(symbol, expiry, lower_strike, "C")
            atm_g   = await get_greeks(symbol, expiry, atm_strike,   "C")
            upper_g = await get_greeks(symbol, expiry, upper_strike, "C")
        except Exception as e:
            logger.warning(f"{symbol} butterfly wing_width={wing_width}: greeks failed — {e}")
            continue
        
        # Net debit = buy lower + buy upper - sell 2x ATM
        net_debit = lower_g["mid"] + upper_g["mid"] - (2 * atm_g["mid"])
        
        if net_debit <= 0:
            logger.info(f"{symbol} butterfly wing_width={wing_width}: net_debit <= 0 — skipping")
            continue
        
        # Max profit = wing width - debit
        max_profit = actual_lower_width - net_debit
        
        if max_profit <= 0:
            logger.info(f"{symbol} butterfly wing_width={wing_width}: max_profit <= 0 — skipping")
            continue
        
        # Debit/width ratio (lower is better, want < 25%)
        debit_ratio = net_debit / actual_lower_width
        
        # Combined delta should be near-neutral
        net_delta = lower_g["delta"] - (2 * atm_g["delta"]) + upper_g["delta"]
        
        logger.info(
            f"{symbol} butterfly: wing_width=${actual_lower_width:.1f} "
            f"strikes={lower_strike}/{atm_strike}/{upper_strike} "
            f"debit=${net_debit:.2f} ratio={debit_ratio:.0%} "
            f"max_profit=${max_profit:.2f} delta={net_delta:.3f}"
        )
        
        return {
            "lower_strike":  lower_strike,
            "body_strike":   atm_strike,
            "upper_strike":  upper_strike,
            "wing_width":    round(actual_lower_width, 2),
            "net_debit":     round(net_debit, 2),
            "debit_ratio":   round(debit_ratio, 4),
            "max_profit":    round(max_profit, 2),
            "max_loss":      round(net_debit, 2),
            "net_delta":     round(net_delta, 4),
            "lower_greeks":  lower_g,
            "body_greeks":   atm_g,
            "upper_greeks":  upper_g,
            "expiry":        expiry,
            "dte":           dte,
        }
    
    logger.warning(f"{symbol} butterfly: no valid structure found across all wing widths")
    return None


async def build_iron_condor(
    symbol:           str,
    underlying_price: float,
    dte_min:          int,
    dte_max:          int,
) -> Optional[dict]:
    logger.info(f"{symbol}: building iron condor (underlying={underlying_price:.2f})")

    chain = await fetch_option_chain(symbol)
    if not chain:
        return None

    expiry = _select_expiry_from_chain(chain, symbol, dte_min, dte_max)
    if not expiry:
        return None

    strikes_data = get_strikes_for_expiry(chain, expiry, symbol)
    if not strikes_data:
        logger.warning(f"{symbol} IC: no strikes for {expiry}")
        return None

    put_wing, call_wing = await asyncio.gather(
        _build_one_wing(symbol, expiry, "P", underlying_price, strikes_data),
        _build_one_wing(symbol, expiry, "C", underlying_price, strikes_data),
    )

    if put_wing is None:
        logger.warning(f"{symbol} IC: put wing failed")
        return None
    if call_wing is None:
        logger.warning(f"{symbol} IC: call wing failed")
        return None

    today        = date.today()
    dte          = (date.fromisoformat(expiry) - today).days
    total_credit = round(put_wing["net_credit"] + call_wing["net_credit"], 2)
    wing_width   = put_wing["spread_width"]
    max_loss     = round(wing_width - total_credit, 2)

    logger.info(
        f"{symbol} IC: put {put_wing['sell_strike']}/{put_wing['buy_strike']} "
        f"({put_wing['credit_ratio']:.0%}) | "
        f"call {call_wing['sell_strike']}/{call_wing['buy_strike']} "
        f"({call_wing['credit_ratio']:.0%}) | "
        f"total=${total_credit:.2f}"
    )

    return {
        "put_sell_strike":   put_wing["sell_strike"],
        "put_buy_strike":    put_wing["buy_strike"],
        "put_credit":        put_wing["net_credit"],
        "put_credit_ratio":  put_wing["credit_ratio"],
        "put_spread_width":  put_wing["spread_width"],
        "put_greeks":        put_wing["greeks"],
        "call_sell_strike":  call_wing["sell_strike"],
        "call_buy_strike":   call_wing["buy_strike"],
        "call_credit":       call_wing["net_credit"],
        "call_credit_ratio": call_wing["credit_ratio"],
        "call_spread_width": call_wing["spread_width"],
        "call_greeks":       call_wing["greeks"],
        "total_credit":      total_credit,
        "wing_width":        wing_width,
        "max_loss":          max_loss,
        "expiry":            expiry,
        "dte":               dte,
    }


async def build_spread(
    symbol:           str,
    structure:        str,
    direction:        str,
    underlying_price: float,
    dte_min:          int,
    dte_max:          int,
) -> Optional[dict]:
    option_type = (
        "P" if structure == "credit" and direction == "bullish" else
        "C" if structure == "credit" and direction == "bearish" else
        "C" if structure == "debit"  and direction == "bullish" else
        "P"
    )

    chain = await fetch_option_chain(symbol)
    if not chain:
        return None

    expiry = _select_expiry_from_chain(chain, symbol, dte_min, dte_max)
    if not expiry:
        return None

    strikes_data = get_strikes_for_expiry(chain, expiry, symbol)
    if not strikes_data:
        logger.warning(f"{symbol}: no strikes for {expiry}")
        return None

    return await find_best_spread(
        symbol, expiry, option_type, structure,
        direction, underlying_price, strikes_data,
    )
