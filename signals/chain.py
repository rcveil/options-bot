"""
signals/chain.py
Option chain scanner: best expiry, best spread, iron condor.

Strike dicts from get_strikes_for_expiry have format:
    {"strike": 700.0, "C": ".SPY260508C700", "P": ".SPY260508P700"}

get_greeks() builds its own OCC symbol internally — we pass strike + option_type.
Single chain fetch per symbol — passed through to all functions.
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
    option_type:      str,       # "C" or "P"
    structure:        str,       # "credit" or "debit"
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


async def build_jade_lizard(
    symbol:           str,
    underlying_price: float,
    dte_min:          int,
    dte_max:          int,
    call_widths:      list[float] = [5.0, 10.0],
) -> Optional[dict]:
    """
    Build jade lizard: short OTM put + short call spread.

    Structure:
      Leg 1: Sell put  at ~0.20 delta (OTM, below market)
      Leg 2: Sell call at ~0.20 delta (OTM, above market)
      Leg 3: Buy  call at short_call_strike + width ($5 or $10)

    The defining rule: total credit > call spread width → zero upside risk.
    Tries $5 width first, then $10 if $5 fails the no-upside-risk check.

    Returns dict with all 3 legs, total credit, upside_risk_free flag.
    """
    logger.info(f"{symbol}: building jade lizard (underlying={underlying_price:.2f})")

    chain = await fetch_option_chain(symbol)
    if not chain:
        return None

    expiry = _select_expiry_from_chain(chain, symbol, dte_min, dte_max)
    if not expiry:
        return None

    strikes_data = get_strikes_for_expiry(chain, expiry, symbol)
    if not strikes_data:
        logger.warning(f"{symbol} JL: no strikes for {expiry}")
        return None

    today        = date.today()
    dte          = (date.fromisoformat(expiry) - today).days
    available    = sorted([s["strike"] for s in strikes_data])

    # ── Leg 1: Short put at ~0.20 delta ───────────────────────────────
    short_put = await _find_short_strike(
        symbol, expiry, "P", "credit", underlying_price, strikes_data,
    )
    if short_put is None:
        logger.warning(f"{symbol} JL: no short put found")
        return None

    put_credit = short_put["greeks"]["mid"]

    # ── Leg 2 + 3: Short call spread at ~0.20 delta ───────────────────
    short_call = await _find_short_strike(
        symbol, expiry, "C", "credit", underlying_price, strikes_data,
    )
    if short_call is None:
        logger.warning(f"{symbol} JL: no short call found")
        return None

    call_credit_solo = short_call["greeks"]["mid"]

    # Try each call spread width — prefer narrowest that satisfies no-upside-risk
    best_result = None

    for width in call_widths:
        target_long_call = short_call["strike"] + width
        long_call_strike = min(
            available,
            key=lambda x: abs(x - target_long_call),
            default=None,
        )
        if long_call_strike is None:
            continue

        actual_width = long_call_strike - short_call["strike"]
        if actual_width < 1.0:
            continue

        try:
            long_call_g = await get_greeks(symbol, expiry, long_call_strike, "C")
        except Exception as e:
            logger.warning(f"{symbol} JL: long call greeks failed at {long_call_strike} — {e}")
            continue

        call_spread_credit = round(call_credit_solo - long_call_g["mid"], 2)
        if call_spread_credit <= 0:
            logger.info(f"{symbol} JL: call spread credit <= 0 at width=${actual_width:.1f} — skipping")
            continue

        total_credit       = round(put_credit + call_spread_credit, 2)
        upside_risk_free   = total_credit > actual_width
        call_spread_ratio  = call_spread_credit / actual_width if actual_width > 0 else 0

        # Downside max loss: unlimited below breakeven
        # Practical max loss (for sizing): short put strike - total credit (breakeven)
        breakeven          = round(short_put["strike"] - total_credit, 2)
        # For position sizing use put strike * 0.20 as representative downside
        sizing_max_loss    = round(short_put["strike"] * 0.20, 2)

        logger.info(
            f"{symbol} JL: put={short_put['strike']} short_call={short_call['strike']} "
            f"long_call={long_call_strike} width=${actual_width:.1f} "
            f"put_credit=${put_credit:.2f} call_spread=${call_spread_credit:.2f} "
            f"total=${total_credit:.2f} upside_risk_free={upside_risk_free}"
        )

        result = {
            # Put leg
            "put_strike":         short_put["strike"],
            "put_credit":         round(put_credit, 2),
            "put_greeks":         short_put["greeks"],
            # Call spread legs
            "short_call_strike":  short_call["strike"],
            "long_call_strike":   long_call_strike,
            "call_spread_width":  round(actual_width, 2),
            "call_spread_credit": call_spread_credit,
            "call_spread_ratio":  round(call_spread_ratio, 4),
            "short_call_greeks":  short_call["greeks"],
            "long_call_greeks":   long_call_g,
            # Combined
            "total_credit":       total_credit,
            "upside_risk_free":   upside_risk_free,
            "breakeven":          breakeven,
            "sizing_max_loss":    sizing_max_loss,
            "expiry":             expiry,
            "dte":                dte,
        }

        if upside_risk_free:
            logger.info(f"{symbol} JL: ✓ zero upside risk — total credit ${total_credit:.2f} > width ${actual_width:.1f}")
            return result

        # Keep as best_result in case no width achieves zero upside risk
        if best_result is None:
            best_result = result

    if best_result:
        logger.warning(
            f"{symbol} JL: no width achieved zero upside risk — "
            f"best total credit ${best_result['total_credit']:.2f} vs "
            f"width ${best_result['call_spread_width']:.1f}. "
            f"filters.py will flag this."
        )
        return best_result

    logger.warning(f"{symbol} JL: could not build any valid structure")
    return None


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


async def build_butterfly(
    symbol:           str,
    underlying_price: float,
    dte_min:          int = 25,
    dte_max:          int = 35,
    wing_widths:      list[float] = [5.0, 7.5, 10.0],
) -> Optional[dict]:
    """
    Build long call butterfly:
      Buy  1 lower strike (ITM)
      Sell 2 body strikes  (ATM)
      Buy  1 upper strike  (OTM)
    Equal wing spacing. Tries $5, $7.5, $10.
    Returns first structure where debit/width <= 30%.
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
        logger.warning(f"{symbol} BF: no strikes for {expiry}")
        return None

    available = sorted([s["strike"] for s in strikes_data])
    today     = date.today()
    dte       = (date.fromisoformat(expiry) - today).days

    # Body strike = closest to current price (ATM)
    body_strike = min(available, key=lambda x: abs(x - underlying_price))

    for wing_width in wing_widths:
        lower_target = body_strike - wing_width
        upper_target = body_strike + wing_width

        lower_strike = min(available, key=lambda x: abs(x - lower_target))
        upper_strike = min(available, key=lambda x: abs(x - upper_target))

        actual_lower = body_strike - lower_strike
        actual_upper = upper_strike - body_strike

        # Wings must be roughly equal
        if abs(actual_lower - actual_upper) > 1.0:
            logger.info(
                f"{symbol} BF: wings unequal at width=${wing_width:.1f} "
                f"(lower={actual_lower:.1f} upper={actual_upper:.1f}) — skipping"
            )
            continue

        actual_wing = round((actual_lower + actual_upper) / 2, 2)

        try:
            lower_g = await get_greeks(symbol, expiry, lower_strike, "C")
            body_g  = await get_greeks(symbol, expiry, body_strike,  "C")
            upper_g = await get_greeks(symbol, expiry, upper_strike, "C")
        except Exception as e:
            logger.warning(f"{symbol} BF wing=${wing_width:.1f}: greeks failed — {e}")
            continue

        # Net debit = buy lower + buy upper - sell 2x body
        net_debit  = round(lower_g["mid"] + upper_g["mid"] - (2 * body_g["mid"]), 2)
        if net_debit <= 0:
            logger.info(f"{symbol} BF wing=${wing_width:.1f}: net_debit={net_debit:.2f} <= 0 — skipping")
            continue

        max_profit   = round(actual_wing - net_debit, 2)
        if max_profit <= 0:
            logger.info(f"{symbol} BF wing=${wing_width:.1f}: max_profit={max_profit:.2f} <= 0 — skipping")
            continue

        debit_ratio  = round(net_debit / actual_wing, 4)
        profit_ratio = round(max_profit / net_debit, 2)
        net_delta    = round(
            lower_g["delta"] - (2 * body_g["delta"]) + upper_g["delta"], 4
        )

        logger.info(
            f"{symbol} BF: {lower_strike}/{body_strike}/{upper_strike} "
            f"wing=${actual_wing:.1f} debit=${net_debit:.2f} "
            f"ratio={debit_ratio:.0%} max_profit=${max_profit:.2f} "
            f"({profit_ratio:.1f}x) delta={net_delta:.3f}"
        )

        return {
            "lower_strike":  lower_strike,
            "body_strike":   body_strike,
            "upper_strike":  upper_strike,
            "wing_width":    actual_wing,
            "net_debit":     net_debit,
            "debit_ratio":   debit_ratio,
            "max_profit":    max_profit,
            "profit_ratio":  profit_ratio,
            "max_loss":      net_debit,   # max loss = debit paid
            "net_delta":     net_delta,
            "lower_greeks":  lower_g,
            "body_greeks":   body_g,
            "upper_greeks":  upper_g,
            "expiry":        expiry,
            "dte":           dte,
        }

    logger.warning(f"{symbol} BF: no valid structure found across all wing widths")
    return None
