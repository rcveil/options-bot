"""
alerts/late_entry.py
Re-evaluates a stored signal against live market data.
Handles both vertical spreads and iron condors.
Called by /check SYMBOL Telegram command.
"""

import logging
from data.tastytrade import get_greeks, get_quote
from data.market import get_vix, classify_vix, get_ivr
from signals.strategy import compute_pop
from alerts.formatter import format_late_entry
from storage.journal import get_latest_signal
from config.thresholds import MIN_CREDIT_WIDTH_RATIO, MIN_POP_CREDIT, MIN_POP_ELEVATED

logger = logging.getLogger(__name__)


async def _reprice_vertical(signal: dict) -> tuple[float, float, float]:
    """
    Re-fetch both legs of a vertical spread.
    Returns (net_credit, spread_width, credit_ratio).
    """
    symbol      = signal["symbol"]
    expiry      = signal["expiry"]
    option_type = signal["option_type"]
    sell_strike = signal["sell_strike"]
    buy_strike  = signal["buy_strike"]
    spread_width = signal.get("spread_width") or abs(sell_strike - buy_strike)

    try:
        short_g = await get_greeks(symbol, expiry, sell_strike, option_type)
        long_g  = await get_greeks(symbol, expiry, buy_strike,  option_type)
        net_credit = round(short_g["mid"] - long_g["mid"], 2)
    except Exception as e:
        logger.warning(f"Both-leg fetch failed, using short only: {e}")
        short_g    = await get_greeks(symbol, expiry, sell_strike, option_type)
        net_credit = short_g["mid"]

    ratio = net_credit / spread_width if spread_width > 0 else 0
    return net_credit, spread_width, ratio


async def _reprice_iron_condor(signal: dict) -> tuple[float, float, float, float, float]:
    """
    Re-fetch all 4 legs of an iron condor.
    Returns (total_credit, put_ratio, call_ratio, wing_width, worst_ratio).
    """
    symbol     = signal["symbol"]
    expiry     = signal["expiry"]
    wing_width = signal.get("wing_width", 5.0)

    put_sell  = signal["put_sell_strike"]
    put_buy   = signal["put_buy_strike"]
    call_sell = signal["call_sell_strike"]
    call_buy  = signal["call_buy_strike"]

    put_short_g  = await get_greeks(symbol, expiry, put_sell,  "P")
    put_long_g   = await get_greeks(symbol, expiry, put_buy,   "P")
    call_short_g = await get_greeks(symbol, expiry, call_sell, "C")
    call_long_g  = await get_greeks(symbol, expiry, call_buy,  "C")

    put_credit  = round(put_short_g["mid"]  - put_long_g["mid"],  2)
    call_credit = round(call_short_g["mid"] - call_long_g["mid"], 2)
    total_credit = round(put_credit + call_credit, 2)

    put_ratio  = put_credit  / wing_width if wing_width > 0 else 0
    call_ratio = call_credit / wing_width if wing_width > 0 else 0
    worst_ratio = min(put_ratio, call_ratio)

    return total_credit, put_ratio, call_ratio, wing_width, worst_ratio


async def evaluate_late_entry(symbol: str) -> str:
    signal = await get_latest_signal(symbol)
    if not signal:
        return f"No recent signal found for {symbol}."

    try:
        vix    = await get_vix()
        regime = classify_vix(vix)
        ivr    = await get_ivr(symbol)
        quote  = await get_quote(symbol)
    except Exception as e:
        return f"Could not fetch live data for {symbol}: {e}"

    min_pop = MIN_POP_ELEVATED if regime == "elevated" else MIN_POP_CREDIT

    stored_vwap = signal.get("vwap", quote["mid"])
    if quote["mid"] > stored_vwap * 1.001:
        price_vs_vwap = "above VWAP"
    elif quote["mid"] < stored_vwap * 0.999:
        price_vs_vwap = "below VWAP"
    else:
        price_vs_vwap = "at VWAP"

    is_ic = signal["strategy"] == "iron_condor"

    # ── Iron condor re-evaluation ──────────────────────────────────────
    if is_ic:
        try:
            total_credit, put_ratio, call_ratio, wing_width, worst_ratio = \
                await _reprice_iron_condor(signal)
        except Exception as e:
            return f"Could not re-price iron condor for {symbol}: {e}"

        # PoP for IC = P(put OTM) × P(call OTM)
        put_pop = compute_pop(
            quote["mid"], signal["put_sell_strike"],
            signal["dte"], signal.get("iv", 0.3), "P"
        )
        call_pop = compute_pop(
            quote["mid"], signal["call_sell_strike"],
            signal["dte"], signal.get("iv", 0.3), "C"
        )
        combined_pop = round(put_pop * call_pop, 4)

        # Verdict based on WORST wing ratio (per-wing rule)
        if worst_ratio >= MIN_CREDIT_WIDTH_RATIO and combined_pop >= min_pop:
            verdict = "valid"
            advice  = (
                f"Both wings still intact. Total credit ${total_credit:.2f}. "
                f"Put ratio {put_ratio:.0%}, call ratio {call_ratio:.0%}. "
                f"Enter at market. Stop at ${total_credit * 2:.2f}."
            )
        elif (worst_ratio >= MIN_CREDIT_WIDTH_RATIO * 0.85
              and combined_pop >= min_pop * 0.95):
            verdict = "marginal"
            advice  = (
                f"One or both wings weakened. "
                f"Put {put_ratio:.0%} / call {call_ratio:.0%}. "
                f"Total credit ${total_credit:.2f}. "
                f"1 contract only. Stop at ${total_credit * 2:.2f}."
            )
        else:
            verdict = "expired"
            advice  = (
                f"Condor has deteriorated — "
                f"put {put_ratio:.0%} / call {call_ratio:.0%}, "
                f"PoP {combined_pop:.0%}. Do not enter."
            )

        return format_late_entry(
            symbol          = symbol,
            strategy        = signal["strategy"],
            original_time   = signal["timestamp_et"],
            original_credit = signal["credit_debit"],
            current_credit  = total_credit,
            current_ratio   = worst_ratio,
            current_pop     = combined_pop,
            current_ivr     = ivr,
            price_vs_vwap   = price_vs_vwap,
            verdict         = verdict,
            advice          = advice,
            put_ratio       = put_ratio,
            call_ratio      = call_ratio,
        )

    # ── Vertical spread re-evaluation ─────────────────────────────────
    try:
        net_credit, spread_width, current_ratio = \
            await _reprice_vertical(signal)
    except Exception as e:
        return f"Could not re-price spread for {symbol}: {e}"

    short_g = await get_greeks(
        symbol, signal["expiry"],
        signal["sell_strike"], signal["option_type"]
    )
    pop = compute_pop(
        quote["mid"], signal["sell_strike"],
        signal["dte"], short_g["iv"], signal["option_type"]
    )

    if current_ratio >= MIN_CREDIT_WIDTH_RATIO and pop >= min_pop:
        verdict = "valid"
        advice  = (
            f"Setup still intact. Net credit ${net_credit:.2f} "
            f"on ${spread_width:.1f}-wide spread ({current_ratio:.0%}). "
            f"Enter at market. Stop at ${net_credit * 2:.2f}."
        )
    elif (current_ratio >= MIN_CREDIT_WIDTH_RATIO * 0.85
          and pop >= min_pop * 0.95):
        verdict = "marginal"
        advice  = (
            f"Credit slipped to ${net_credit:.2f} "
            f"(ratio {current_ratio:.0%}, just below 30%). "
            f"1 contract only. Stop at ${net_credit * 2:.2f}."
        )
    else:
        verdict = "expired"
        advice  = (
            f"Setup deteriorated — credit ${net_credit:.2f}, "
            f"ratio {current_ratio:.0%}, PoP {pop:.0%}. Do not enter."
        )

    return format_late_entry(
        symbol          = symbol,
        strategy        = signal["strategy"],
        original_time   = signal["timestamp_et"],
        original_credit = signal["credit_debit"],
        current_credit  = net_credit,
        current_ratio   = current_ratio,
        current_pop     = pop,
        current_ivr     = ivr,
        price_vs_vwap   = price_vs_vwap,
        verdict         = verdict,
        advice          = advice,
    )
