"""
signals/chain.py
Option chain scanner with detailed INFO logging at every decision point.
"""

import asyncio
import logging
from datetime import date
from typing import Optional

"from data.tastytrade import get_session, get_greeks, get_option_chain_strikes
from data.tastytrade import (
    get_greeks, fetch_option_chain, get_strikes_for_expiry
)
from signals.strategy import compute_pop
from config.thresholds import (
    DELTA_SHORT_CREDIT_MIN, DELTA_SHORT_CREDIT_MAX,
    DELTA_LONG_DEBIT_MIN,   DELTA_LONG_DEBIT_MAX,
    DTE_CREDIT_MIN, DTE_CREDIT_MAX,
    DTE_DEBIT_MIN,  DTE_DEBIT_MAX,
)

logger = logging.getLogger(__name__)

CANDIDATE_WIDTHS = [2.5, 5, 7.5, 10, 12.5, 15, 20, 25]
MIN_CREDIT_RATIO = 0.33


async def select_expiry(symbol: str, dte_min: int, dte_max: int) -> Optional[str]:
    session = await get_session()
    try:
        from tastytrade.instruments import get_option_chain
        chain = await asyncio.wait_for(
            get_option_chain(session, symbol), timeout=10.0
        )
    except Exception as e:
        logger.error(f"{symbol}: option chain fetch failed — {e}")
        return None

    today      = date.today()
    candidates = [
        ((exp - today).days, exp)
        for exp in chain.keys()
        if dte_min <= (exp - today).days <= dte_max
    ]

    if not candidates:
        all_dtes = sorted([(exp - today).days for exp in chain.keys()])
        logger.warning(
            f"{symbol}: no expiry in DTE {dte_min}–{dte_max}. "
            f"Available DTEs: {all_dtes[:8]}"
        )
        return None

    candidates.sort(key=lambda x: x[0])
    chosen = candidates[0][1]
    logger.info(f"{symbol}: selected expiry {chosen} ({candidates[0][0]} DTE)")
    return str(chosen)


async def _find_short_strike(
    symbol: str, expiry: str, option_type: str,
    structure: str, underlying_price: float,
) -> Optional[dict]:
    delta_min = DELTA_SHORT_CREDIT_MIN if structure == "credit" else DELTA_LONG_DEBIT_MIN
    delta_max = DELTA_SHORT_CREDIT_MAX if structure == "credit" else DELTA_LONG_DEBIT_MAX

    strikes_data = await get_strikes_for_expiry(symbol, expiry)
    if not strikes_data:
        logger.warning(f"{symbol}: empty strike list for {expiry}")
        return None

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
        f"for delta {delta_min}–{delta_max} "
        f"(underlying={underlying_price:.2f})"
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
            logger.warning(f"{symbol} {sd['strike']}: zero bid — illiquid, skipping")
            continue

        diff = abs(abs_delta - target_delta)
        if diff < best_delta_diff:
            best_delta_diff = diff
            best = {"strike": sd["strike"], "greeks": g}

    if best:
        logger.info(
            f"{symbol}: best short strike {best['strike']} "
            f"delta={best['greeks']['delta']:.3f} "
            f"mid=${best['greeks']['mid']:.2f}"
        )
    else:
        logger.warning(
            f"{symbol}: no strike found with delta {delta_min}–{delta_max}. "
            f"All candidates had wrong delta or zero bid."
        )

    return best


async def _find_long_strike(
    symbol: str, expiry: str, option_type: str,
    short_strike: float, short_credit: float,
    strikes_data: list[dict],
) -> Optional[dict]:
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
            logger.info(
                f"{symbol}: width=${actual_width:.1f} — "
                f"net credit ${net_credit:.2f} negative (long costs more than short)"
            )
            continue

        ratio = net_credit / actual_width
        logger.info(
            f"{symbol}: width=${actual_width:.1f} "
            f"short={short_strike} long={closest} "
            f"credit=${net_credit:.2f} ratio={ratio:.0%} "
            f"{'✓ PASS' if ratio >= MIN_CREDIT_RATIO else '✗ below 33%'}"
        )

        result = {
            "long_strike":  closest,
            "spread_width": actual_width,
            "long_greeks":  long_g,
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
            f"did not reach 33% — filters.py will reject. "
            f"Consider that IV may be too low for a credit spread today."
        )
    else:
        logger.warning(f"{symbol}: no long strike candidates found at any width")

    return best_result


async def _build_one_wing(
    symbol: str, expiry: str, option_type: str, underlying_price: float,
) -> Optional[dict]:
    logger.info(f"{symbol}: building {option_type} wing for {expiry}")
    short = await _find_short_strike(
        symbol, expiry, option_type, "credit", underlying_price
    )
    if short is None:
        return None

    strikes_data = await get_option_chain_strikes(symbol, expiry)
    if not strikes_data:
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
    symbol: str, expiry: str, option_type: str,
    structure: str, direction: str, underlying_price: float,
) -> Optional[dict]:
    logger.info(
        f"{symbol}: finding {structure} {option_type} spread "
        f"for {expiry} (direction={direction})"
    )
    today = date.today()
    dte   = (date.fromisoformat(expiry) - today).days

    short = await _find_short_strike(
        symbol, expiry, option_type, structure, underlying_price
    )
    if short is None:
        return None

    strikes_data = await get_strikes_for_expiry(symbol, expiry)
    if not strikes_data:
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
    max_loss     = spread_width - net_credit

    logger.info(
        f"{symbol}: final spread {short['strike']}/{long_result['long_strike']} "
        f"width=${spread_width:.1f} credit=${net_credit:.2f} "
        f"ratio={credit_ratio:.0%} max_loss=${max_loss:.2f}"
    )

    return {
        "sell_strike":   short["strike"],
        "buy_strike":    long_result["long_strike"],
        "spread_width":  spread_width,
        "net_credit":    net_credit,
        "credit_ratio":  credit_ratio,
        "max_loss":      round(max_loss, 2),
        "greeks":        short["greeks"],
        "expiry":        expiry,
        "dte":           dte,
    }


async def build_iron_condor(
    symbol: str, underlying_price: float,
    dte_min: int, dte_max: int,
) -> Optional[dict]:
    logger.info(f"{symbol}: building iron condor (underlying={underlying_price:.2f})")
    expiry = await select_expiry(symbol, dte_min, dte_max)
    if not expiry:
        return None

    put_wing, call_wing = await asyncio.gather(
        _build_one_wing(symbol, expiry, "P", underlying_price),
        _build_one_wing(symbol, expiry, "C", underlying_price),
    )

    if put_wing is None:
        logger.warning(f"{symbol} IC: put wing returned None — skipping condor")
        return None
    if call_wing is None:
        logger.warning(f"{symbol} IC: call wing returned None — skipping condor")
        return None

    today        = date.today()
    dte          = (date.fromisoformat(expiry) - today).days
    total_credit = round(put_wing["net_credit"] + call_wing["net_credit"], 2)
    wing_width   = put_wing["spread_width"]
    max_loss     = round(wing_width - total_credit, 2)

    logger.info(
        f"{symbol} IC: "
        f"put {put_wing['sell_strike']}/{put_wing['buy_strike']} "
        f"({put_wing['credit_ratio']:.0%}) | "
        f"call {call_wing['sell_strike']}/{call_wing['buy_strike']} "
        f"({call_wing['credit_ratio']:.0%}) | "
        f"total=${total_credit:.2f} max_loss=${max_loss:.2f}"
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
    symbol: str, structure: str, direction: str,
    underlying_price: float, dte_min: int, dte_max: int,
) -> Optional[dict]:
    option_type = (
        "P" if structure == "credit" and direction == "bullish" else
        "C" if structure == "credit" and direction == "bearish" else
        "C" if structure == "debit"  and direction == "bullish" else
        "P"
    )
    expiry = await select_expiry(symbol, dte_min, dte_max)
    if not expiry:
        return None
    return await find_best_spread(
        symbol, expiry, option_type, structure, direction, underlying_price
    )
