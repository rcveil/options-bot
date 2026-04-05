"""
alerts/late_entry.py
Re-evaluates a stored signal against live market data.
Called by /check SYMBOL Telegram command.
"""

import logging
from data.tastytrade import get_greeks, get_quote
from data.market import get_vix, classify_vix, get_ivr
from signals.strategy import compute_pop
from alerts.formatter import format_late_entry
from storage.journal import get_latest_signal
from config.settings import ACCOUNT_SIZE
from config.thresholds import MIN_CREDIT_WIDTH_RATIO, MIN_POP_CREDIT, MIN_POP_ELEVATED

logger = logging.getLogger(__name__)


async def evaluate_late_entry(symbol: str) -> str:
    signal = await get_latest_signal(symbol)
    if not signal:
        return f"No recent signal found for {symbol}."

    try:
        vix    = await get_vix()
        regime = classify_vix(vix)
        ivr    = await get_ivr(symbol)
        quote  = await get_quote(symbol)
        greeks = await get_greeks(
            symbol,
            signal["expiry"],
            signal["sell_strike"],
            signal["option_type"],
        )
    except Exception as e:
        logger.error(f"Late entry data fetch failed for {symbol}: {e}")
        return f"Could not fetch live data for {symbol}: {e}"

    current_mid  = greeks["mid"]
    spread_width = abs(signal["sell_strike"] - signal["buy_strike"])
    current_ratio = current_mid / spread_width if spread_width > 0 else 0

    pop = compute_pop(
        underlying_price = quote["mid"],
        strike           = signal["sell_strike"],
        dte              = signal["dte"],
        iv               = greeks["iv"],
        option_type      = signal["option_type"],
    )

    stored_vwap = signal.get("vwap", quote["mid"])
    if quote["mid"] > stored_vwap * 1.001:
        price_vs_vwap = "above VWAP"
    elif quote["mid"] < stored_vwap * 0.999:
        price_vs_vwap = "below VWAP"
    else:
        price_vs_vwap = "at VWAP"

    min_pop = MIN_POP_ELEVATED if regime == "elevated" else MIN_POP_CREDIT

    if current_ratio >= MIN_CREDIT_WIDTH_RATIO and pop >= min_pop:
        verdict = "valid"
        advice  = (
            f"Setup still intact. Enter at ${current_mid:.2f} mid. "
            f"Set stop at ${current_mid * 2:.2f}."
        )
    elif current_ratio >= MIN_CREDIT_WIDTH_RATIO * 0.85 and pop >= min_pop * 0.95:
        verdict = "marginal"
        advice  = (
            f"Credit slipped to ${current_mid:.2f} "
            f"(ratio {current_ratio:.0%}, just below threshold). "
            f"If entering, use 1 contract only. "
            f"Stop at ${current_mid * 2:.2f}."
        )
    else:
        verdict = "expired"
        advice  = (
            f"Setup has deteriorated — credit/width {current_ratio:.0%}, "
            f"PoP {pop:.0%}. Do not enter."
        )

    return format_late_entry(
        symbol           = symbol,
        strategy         = signal["strategy"],
        original_time    = signal["timestamp_et"],
        original_credit  = signal["credit_debit"],
        current_credit   = current_mid,
        current_ratio    = current_ratio,
        current_pop      = pop,
        current_ivr      = ivr,
        price_vs_vwap    = price_vs_vwap,
        verdict          = verdict,
        advice           = advice,
    )
