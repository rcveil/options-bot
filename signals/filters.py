"""
signals/filters.py
Entry gate checks for credit and debit spreads.
All thresholds pulled from config/thresholds.py.
"""

from dataclasses import dataclass, field
from config.thresholds import (
    MIN_CREDIT_WIDTH_RATIO, MIN_POP_CREDIT, MIN_POP_ELEVATED,
    MAX_BID_ASK_PCT, MIN_OPEN_INTEREST, MIN_OPEN_INTEREST_SPX,
    DELTA_SHORT_CREDIT_MIN, DELTA_SHORT_CREDIT_MAX,
    DELTA_LONG_DEBIT_MIN, DELTA_LONG_DEBIT_MAX,
    DTE_CREDIT_MIN, DTE_CREDIT_MAX,
    DTE_DEBIT_MIN, DTE_DEBIT_MAX,
)

INDEX_SYMBOLS = {"SPX", "SPY", "QQQ"}


@dataclass
class FilterResult:
    passed:   bool
    reasons:  list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def check_credit_spread(
    credit:         float,
    width:          float,
    pop:            float,
    bid_ask_spread: float,
    mid_price:      float,
    short_delta:    float,
    dte:            int,
    open_interest:  int,
    symbol:         str,
    vix_regime:     str,
) -> FilterResult:
    reasons  = []
    warnings = []

    # Credit / width ratio
    ratio = credit / width if width > 0 else 0
    if ratio < MIN_CREDIT_WIDTH_RATIO:
        reasons.append(
            f"Credit/width {ratio:.0%} below 30% minimum "
            f"(collected ${credit:.2f} on ${width:.0f}-wide spread)"
        )

    # PoP
    min_pop = MIN_POP_ELEVATED if vix_regime == "elevated" else MIN_POP_CREDIT
    if pop < min_pop:
        reasons.append(f"PoP {pop:.0%} below {min_pop:.0%} minimum")

    # Bid/ask spread relative to mid
    ba_pct = bid_ask_spread / mid_price if mid_price > 0 else 1.0
    if ba_pct > MAX_BID_ASK_PCT:
        reasons.append(
            f"Bid/ask ${bid_ask_spread:.2f} is {ba_pct:.0%} of mid — "
            f"too wide, liquidity insufficient"
        )
    elif ba_pct > 0.06:
        warnings.append(
            f"Bid/ask slightly wide ({ba_pct:.0%}) — use limit order at mid"
        )

    # Delta of short leg
    abs_delta = abs(short_delta)
    if not (DELTA_SHORT_CREDIT_MIN <= abs_delta <= DELTA_SHORT_CREDIT_MAX):
        reasons.append(
            f"Short leg delta {short_delta:.2f} outside "
            f"{DELTA_SHORT_CREDIT_MIN}–{DELTA_SHORT_CREDIT_MAX} range"
        )

    # DTE
    if not (DTE_CREDIT_MIN <= dte <= DTE_CREDIT_MAX):
        reasons.append(
            f"DTE {dte} outside {DTE_CREDIT_MIN}–{DTE_CREDIT_MAX} "
            f"range for credit spreads"
        )

    # Open interest
    min_oi = MIN_OPEN_INTEREST_SPX if symbol in INDEX_SYMBOLS \
             else MIN_OPEN_INTEREST
    if open_interest < min_oi:
        reasons.append(
            f"Open interest {open_interest:,} below {min_oi:,} minimum"
        )

    # VIX regime warnings
    if vix_regime == "elevated":
        warnings.append(
            "VIX elevated — reduce to 1 contract, widen strikes, "
            "raise PoP bar to 70%"
        )
    elif vix_regime == "spike":
        warnings.append(
            "VIX spike — index only mode, 1 contract max"
        )

    return FilterResult(
        passed   = len(reasons) == 0,
        reasons  = reasons,
        warnings = warnings,
    )


def check_debit_spread(
    debit:          float,
    width:          float,
    pop:            float,
    bid_ask_spread: float,
    mid_price:      float,
    long_delta:     float,
    dte:            int,
    open_interest:  int,
    symbol:         str,
    vix_regime:     str,
) -> FilterResult:
    reasons  = []
    warnings = []

    # Debit must not exceed 70% of width (long leg not too expensive)
    debit_ratio = debit / width if width > 0 else 1.0
    if debit_ratio > 0.70:
        reasons.append(
            f"Debit ${debit:.2f} is {debit_ratio:.0%} of width — "
            f"long leg too expensive relative to short leg"
        )

    # PoP
    if pop < 0.50:
        reasons.append(
            f"PoP {pop:.0%} below 50% minimum for debit spreads"
        )

    # Bid/ask
    ba_pct = bid_ask_spread / mid_price if mid_price > 0 else 1.0
    if ba_pct > MAX_BID_ASK_PCT:
        reasons.append(
            f"Bid/ask too wide: {ba_pct:.0%} of mid price"
        )

    # Delta of long leg
    abs_delta = abs(long_delta)
    if not (DELTA_LONG_DEBIT_MIN <= abs_delta <= DELTA_LONG_DEBIT_MAX):
        reasons.append(
            f"Long leg delta {long_delta:.2f} outside "
            f"{DELTA_LONG_DEBIT_MIN}–{DELTA_LONG_DEBIT_MAX} range"
        )

    # DTE
    if not (DTE_DEBIT_MIN <= dte <= DTE_DEBIT_MAX):
        reasons.append(
            f"DTE {dte} outside {DTE_DEBIT_MIN}–{DTE_DEBIT_MAX} "
            f"range for debit spreads"
        )

    # Open interest
    min_oi = MIN_OPEN_INTEREST_SPX if symbol in INDEX_SYMBOLS \
             else MIN_OPEN_INTEREST
    if open_interest < min_oi:
        reasons.append(
            f"Open interest {open_interest:,} below {min_oi:,} minimum"
        )

    if vix_regime in ("elevated", "spike"):
        warnings.append(
            "VIX elevated — debit spreads carry extra risk, "
            "consider skipping or using minimum size"
        )

    return FilterResult(
        passed   = len(reasons) == 0,
        reasons  = reasons,
        warnings = warnings,
    )
