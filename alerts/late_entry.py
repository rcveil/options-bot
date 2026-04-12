"""
alerts/late_entry.py
Re-evaluates a stored signal against live market data.
Correctly computes net credit from both legs of the spread.
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

    sell_strike = signal["sell_strike"]
    buy_strike  = signal["buy_strike"]
    option_type = signal["option_type"]
    expiry      = signal["expiry"]
    dte         = signal["dte"]

    # Spread width — use stored value if available, else compute from strikes
    spread_width = signal.get("spread_width") or abs(sell_strike - buy_strike)

    # Fetch current greeks for BOTH legs to get accurate net credit
    try:
        short_greeks = await get_greeks(
            symbol, expiry, sell_strike, option_type
        )
        long_greeks = await get_greeks(
            symbol, expiry, buy_strike, option_type
        )
        current_net_credit = round(
            short_greeks["mid"] - long_greeks["mid"], 2
        )
    except Exception as e:
        logger.warning(
            f"Could not fetch both legs for {symbol}, "
            f"falling back to short leg only: {e}"
        )
        try:
            short_greeks = await get_greeks(
                symbol, expiry, sell_strike, option_type
            )
            current_net_credit = short_greeks["mid"]
        except Exception as e2:
            return f"Could not fetch greeks for {symbol}: {e2}"

    current_ratio = (
        current_net_credit / spread_width if spread_width > 0 else 0
    )

    pop = compute_pop(
        underlying_price = quote["mid"],
        strike           = sell_strike,
        dte              = dte,
        iv               = short_greeks["iv"],
        option_type      = option_type,
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
            f"Setup still intact. "
            f"Net credit now ${current_net_credit:.2f} "
            f"on ${spread_width:.1f}-wide spread ({current_ratio:.0%}). "
            f"Enter at market. Stop at ${current_net_credit * 2:.2f}."
        )
    elif (
        current_ratio >= MIN_CREDIT_WIDTH_RATIO * 0.85
        and pop >= min_pop * 0.95
    ):
        verdict = "marginal"
        advice  = (
            f"Credit slipped to ${current_net_credit:.2f} "
            f"(ratio {current_ratio:.0%}, just below 30% threshold). "
            f"If entering, use 1 contract only. "
            f"Stop at ${current_net_credit * 2:.2f}."
        )
    else:
        verdict = "expired"
        advice  = (
            f"Setup has deteriorated — "
            f"net credit ${current_net_credit:.2f}, "
            f"ratio {current_ratio:.0%}, PoP {pop:.0%}. "
            f"Do not enter."
        )

    return format_late_entry(
        symbol           = symbol,
        strategy         = signal["strategy"],
        original_time    = signal["timestamp_et"],
        original_credit  = signal["credit_debit"],
        current_credit   = current_net_credit,
        current_ratio    = current_ratio,
        current_pop      = pop,
        current_ivr      = ivr,
        price_vs_vwap    = price_vs_vwap,
        verdict          = verdict,
        advice           = advice,
    )
