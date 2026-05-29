"""
signals/filters.py
Entry gate checks for credit spreads, debit spreads, and iron condors.
Iron condor: each wing is validated independently against the per-wing 1/3 rule.
"""

from dataclasses import dataclass, field
from config.thresholds import (
    MIN_CREDIT_WIDTH_RATIO, MIN_POP_CREDIT, MIN_POP_ELEVATED,
    MIN_POP_IC_COMBINED, MIN_POP_IC_COMBINED_ELEV,
    MAX_BUTTERFLY_DEBIT_RATIO, MIN_BUTTERFLY_PROFIT_RATIO,
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


def _vix_warnings(vix_regime: str) -> list[str]:
    if vix_regime == "elevated":
        return ["VIX elevated — reduce to 1 contract, raise PoP bar to 70%"]
    if vix_regime == "spike":
        return ["VIX spike — index only, 1 contract max"]
    return []


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
    warnings = _vix_warnings(vix_regime)

    ratio = credit / width if width > 0 else 0
    if ratio < MIN_CREDIT_WIDTH_RATIO:
        reasons.append(
            f"Credit/width {ratio:.0%} below 30% minimum "
            f"(${credit:.2f} on ${width:.1f}-wide spread)"
        )

    min_pop = MIN_POP_ELEVATED if vix_regime == "elevated" else MIN_POP_CREDIT
    if pop < min_pop:
        reasons.append(f"PoP {pop:.0%} below {min_pop:.0%} minimum")

    ba_pct = bid_ask_spread / mid_price if mid_price > 0 else 1.0
    if ba_pct > MAX_BID_ASK_PCT:
        reasons.append(
            f"Bid/ask ${bid_ask_spread:.2f} is {ba_pct:.0%} of mid — too wide"
        )
    elif ba_pct > 0.06:
        warnings.append(f"Bid/ask slightly wide ({ba_pct:.0%}) — use limit at mid")

    abs_delta = abs(short_delta)
    if not (DELTA_SHORT_CREDIT_MIN <= abs_delta <= DELTA_SHORT_CREDIT_MAX):
        reasons.append(
            f"Short delta {short_delta:.2f} outside "
            f"{DELTA_SHORT_CREDIT_MIN}–{DELTA_SHORT_CREDIT_MAX}"
        )

    if not (DTE_CREDIT_MIN <= dte <= DTE_CREDIT_MAX):
        reasons.append(f"DTE {dte} outside {DTE_CREDIT_MIN}–{DTE_CREDIT_MAX}")

    min_oi = MIN_OPEN_INTEREST_SPX if symbol in INDEX_SYMBOLS \
             else MIN_OPEN_INTEREST
    if open_interest < min_oi:
        reasons.append(f"OI {open_interest:,} below {min_oi:,} minimum")

    return FilterResult(passed=len(reasons) == 0,
                        reasons=reasons, warnings=warnings)


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
    warnings = _vix_warnings(vix_regime)

    debit_ratio = debit / width if width > 0 else 1.0
    if debit_ratio > 0.70:
        reasons.append(
            f"Debit ${debit:.2f} is {debit_ratio:.0%} of width — "
            f"long leg too expensive"
        )

    if pop < 0.50:
        reasons.append(f"PoP {pop:.0%} below 50% minimum for debit spreads")

    ba_pct = bid_ask_spread / mid_price if mid_price > 0 else 1.0
    if ba_pct > MAX_BID_ASK_PCT:
        reasons.append(f"Bid/ask too wide: {ba_pct:.0%} of mid")

    abs_delta = abs(long_delta)
    if not (DELTA_LONG_DEBIT_MIN <= abs_delta <= DELTA_LONG_DEBIT_MAX):
        reasons.append(
            f"Long delta {long_delta:.2f} outside "
            f"{DELTA_LONG_DEBIT_MIN}–{DELTA_LONG_DEBIT_MAX}"
        )

    if not (DTE_DEBIT_MIN <= dte <= DTE_DEBIT_MAX):
        reasons.append(f"DTE {dte} outside {DTE_DEBIT_MIN}–{DTE_DEBIT_MAX}")

    min_oi = MIN_OPEN_INTEREST_SPX if symbol in INDEX_SYMBOLS \
             else MIN_OPEN_INTEREST
    if open_interest < min_oi:
        reasons.append(f"OI {open_interest:,} below {min_oi:,} minimum")

    if vix_regime in ("elevated", "spike"):
        warnings.append(
            "VIX elevated — debit spreads carry extra risk, consider skipping"
        )

    return FilterResult(passed=len(reasons) == 0,
                        reasons=reasons, warnings=warnings)


def check_iron_condor(
    # Put wing
    put_credit:       float,
    put_width:        float,
    put_delta:        float,
    put_bid_ask:      float,
    put_mid:          float,
    put_oi:           int,
    # Call wing
    call_credit:      float,
    call_width:       float,
    call_delta:       float,
    call_bid_ask:     float,
    call_mid:         float,
    call_oi:          int,
    # Shared
    pop:              float,
    dte:              int,
    symbol:           str,
    vix_regime:       str,
) -> FilterResult:
    """
    Validate iron condor with per-wing checks.
    Each wing must independently pass the 1/3 credit rule and delta gate.
    Combined PoP and DTE are checked once.
    """
    reasons  = []
    warnings = _vix_warnings(vix_regime)

    min_oi  = MIN_OPEN_INTEREST_SPX if symbol in INDEX_SYMBOLS \
              else MIN_OPEN_INTEREST
    min_pop = MIN_POP_IC_COMBINED_ELEV if vix_regime == "elevated" else MIN_POP_IC_COMBINED

    # ── Put wing checks ────────────────────────────────────────────────
    put_ratio = put_credit / put_width if put_width > 0 else 0
    if put_ratio < MIN_CREDIT_WIDTH_RATIO:
        reasons.append(
            f"Put wing credit/width {put_ratio:.0%} below 30% "
            f"(${put_credit:.2f} on ${put_width:.1f}-wide)"
        )

    put_ba_pct = put_bid_ask / put_mid if put_mid > 0 else 1.0
    if put_ba_pct > MAX_BID_ASK_PCT:
        reasons.append(f"Put wing bid/ask too wide: {put_ba_pct:.0%} of mid")
    elif put_ba_pct > 0.06:
        warnings.append("Put wing bid/ask slightly wide — use limit at mid")

    abs_put_delta = abs(put_delta)
    if not (DELTA_SHORT_CREDIT_MIN <= abs_put_delta <= DELTA_SHORT_CREDIT_MAX):
        reasons.append(
            f"Put short delta {put_delta:.2f} outside "
            f"{DELTA_SHORT_CREDIT_MIN}–{DELTA_SHORT_CREDIT_MAX}"
        )

    if put_oi < min_oi:
        reasons.append(f"Put wing OI {put_oi:,} below {min_oi:,} minimum")

    # ── Call wing checks ───────────────────────────────────────────────
    call_ratio = call_credit / call_width if call_width > 0 else 0
    if call_ratio < MIN_CREDIT_WIDTH_RATIO:
        reasons.append(
            f"Call wing credit/width {call_ratio:.0%} below 30% "
            f"(${call_credit:.2f} on ${call_width:.1f}-wide)"
        )

    call_ba_pct = call_bid_ask / call_mid if call_mid > 0 else 1.0
    if call_ba_pct > MAX_BID_ASK_PCT:
        reasons.append(f"Call wing bid/ask too wide: {call_ba_pct:.0%} of mid")
    elif call_ba_pct > 0.06:
        warnings.append("Call wing bid/ask slightly wide — use limit at mid")

    abs_call_delta = abs(call_delta)
    if not (DELTA_SHORT_CREDIT_MIN <= abs_call_delta <= DELTA_SHORT_CREDIT_MAX):
        reasons.append(
            f"Call short delta {call_delta:.2f} outside "
            f"{DELTA_SHORT_CREDIT_MIN}–{DELTA_SHORT_CREDIT_MAX}"
        )

    if call_oi < min_oi:
        reasons.append(f"Call wing OI {call_oi:,} below {min_oi:,} minimum")

    # ── Combined checks ────────────────────────────────────────────────
    if pop < min_pop:
        reasons.append(
            f"Combined PoP {pop:.0%} below {min_pop:.0%} minimum"
        )

    if not (DTE_CREDIT_MIN <= dte <= DTE_CREDIT_MAX):
        reasons.append(f"DTE {dte} outside {DTE_CREDIT_MIN}–{DTE_CREDIT_MAX}")

    # Wings should be roughly equal width for symmetric condor
    if abs(put_width - call_width) > 5:
        warnings.append(
            f"Wings are unequal: put ${put_width:.1f} vs call ${call_width:.1f} — "
            f"verify this is intentional"
        )

    return FilterResult(passed=len(reasons) == 0,
                        reasons=reasons, warnings=warnings)


def check_jade_lizard(
    # Put leg
    put_credit:         float,
    put_delta:          float,
    put_bid_ask:        float,
    put_mid:            float,
    put_oi:             int,
    # Call spread
    call_spread_credit: float,
    call_spread_width:  float,
    short_call_delta:   float,
    call_bid_ask:       float,
    call_mid:           float,
    call_oi:            int,
    # Combined
    total_credit:       float,
    upside_risk_free:   bool,
    pop_put:            float,
    pop_call:           float,
    dte:                int,
    symbol:             str,
    vix_regime:         str,
) -> FilterResult:
    """
    Jade lizard filter gates.

    Defining rule (hard gate): total credit > call spread width → zero upside risk.
    Put leg: delta, liquidity, PoP checked independently.
    Call spread: credit/width >= 30%, delta, liquidity checked independently.
    Combined: DTE. Call PoP below threshold is a warning not a rejection
    (call side has defined risk regardless).
    """
    reasons  = []
    warnings = _vix_warnings(vix_regime)

    min_oi  = MIN_OPEN_INTEREST_SPX if symbol in INDEX_SYMBOLS \
              else MIN_OPEN_INTEREST
    min_pop = MIN_POP_ELEVATED if vix_regime == "elevated" else MIN_POP_CREDIT

    # ── Defining rule: zero upside risk ───────────────────────────────
    if not upside_risk_free:
        reasons.append(
            f"Total credit ${total_credit:.2f} does not exceed call spread "
            f"width ${call_spread_width:.1f} — upside risk not eliminated"
        )

    # ── Put leg ────────────────────────────────────────────────────────
    abs_put_delta = abs(put_delta)
    if not (DELTA_SHORT_CREDIT_MIN <= abs_put_delta <= DELTA_SHORT_CREDIT_MAX):
        reasons.append(
            f"Put delta {put_delta:.2f} outside "
            f"{DELTA_SHORT_CREDIT_MIN}–{DELTA_SHORT_CREDIT_MAX}"
        )

    put_ba_pct = put_bid_ask / put_mid if put_mid > 0 else 1.0
    if put_ba_pct > MAX_BID_ASK_PCT:
        reasons.append(f"Put bid/ask {put_ba_pct:.0%} of mid — too wide")
    elif put_ba_pct > 0.06:
        warnings.append("Put bid/ask slightly wide — use limit at mid")

    if put_oi < min_oi:
        reasons.append(f"Put OI {put_oi:,} below {min_oi:,} minimum")

    if pop_put < min_pop:
        reasons.append(f"Put PoP {pop_put:.0%} below {min_pop:.0%} minimum")

    # ── Call spread ────────────────────────────────────────────────────
    call_ratio = call_spread_credit / call_spread_width \
                 if call_spread_width > 0 else 0
    if call_ratio < MIN_CREDIT_WIDTH_RATIO:
        reasons.append(
            f"Call spread credit/width {call_ratio:.0%} below 30% "
            f"(${call_spread_credit:.2f} on ${call_spread_width:.1f}-wide)"
        )

    abs_call_delta = abs(short_call_delta)
    if not (DELTA_SHORT_CREDIT_MIN <= abs_call_delta <= DELTA_SHORT_CREDIT_MAX):
        reasons.append(
            f"Short call delta {short_call_delta:.2f} outside "
            f"{DELTA_SHORT_CREDIT_MIN}–{DELTA_SHORT_CREDIT_MAX}"
        )

    call_ba_pct = call_bid_ask / call_mid if call_mid > 0 else 1.0
    if call_ba_pct > MAX_BID_ASK_PCT:
        reasons.append(f"Call bid/ask {call_ba_pct:.0%} of mid — too wide")
    elif call_ba_pct > 0.06:
        warnings.append("Call bid/ask slightly wide — use limit at mid")

    if call_oi < min_oi:
        reasons.append(f"Call OI {call_oi:,} below {min_oi:,} minimum")

    # ── Combined ───────────────────────────────────────────────────────
    if not (DTE_CREDIT_MIN <= dte <= DTE_CREDIT_MAX):
        reasons.append(f"DTE {dte} outside {DTE_CREDIT_MIN}–{DTE_CREDIT_MAX}")

    if pop_call < min_pop:
        warnings.append(
            f"Call PoP {pop_call:.0%} below {min_pop:.0%} — "
            f"stock trending toward short call"
        )

    return FilterResult(
        passed   = len(reasons) == 0,
        reasons  = reasons,
        warnings = warnings,
    )


def check_butterfly(
    debit:          float,
    wing_width:     float,
    max_profit:     float,
    net_delta:      float,
    body_bid_ask:   float,
    body_mid:       float,
    dte:            int,
    open_interest:  int,
    symbol:         str,
    vix_regime:     str,
) -> FilterResult:
    """
    Long butterfly filter gates.
    - Debit/width < 30% (cheap entry relative to structure)
    - Max profit / debit >= 1.5x (reward/risk ratio)
    - Net delta near-neutral (|delta| < 0.15)
    - Body bid/ask <= 10% of mid
    - DTE 25–35 days
    - OI >= minimum
    """
    reasons  = []
    warnings = _vix_warnings(vix_regime)

    min_oi      = MIN_OPEN_INTEREST_SPX if symbol in INDEX_SYMBOLS \
                  else MIN_OPEN_INTEREST
    debit_ratio  = debit / wing_width if wing_width > 0 else 999
    profit_ratio = max_profit / debit if debit > 0 else 0
    ba_pct       = body_bid_ask / body_mid if body_mid > 0 else 999

    if debit_ratio > MAX_BUTTERFLY_DEBIT_RATIO:
        reasons.append(
            f"Debit/width {debit_ratio:.0%} above {MAX_BUTTERFLY_DEBIT_RATIO:.0%} maximum"
        )

    if profit_ratio < MIN_BUTTERFLY_PROFIT_RATIO:
        reasons.append(
            f"Max profit/debit {profit_ratio:.1f}x below {MIN_BUTTERFLY_PROFIT_RATIO:.1f}x minimum"
        )

    if abs(net_delta) > 0.15:
        warnings.append(
            f"Net delta {net_delta:.3f} not fully neutral — slight directional risk"
        )

    if ba_pct > MAX_BID_ASK_PCT:
        reasons.append(f"Body bid/ask {ba_pct:.0%} of mid — too wide")
    elif ba_pct > 0.06:
        warnings.append("Body bid/ask slightly wide — use limit at mid")

    if dte < 25 or dte > 35:
        reasons.append(f"DTE {dte} outside 25–35 butterfly window")

    if open_interest < min_oi:
        reasons.append(f"OI {open_interest:,} below {min_oi:,} minimum")

    return FilterResult(
        passed   = len(reasons) == 0,
        reasons  = reasons,
        warnings = warnings,
    )
