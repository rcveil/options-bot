"""
signals/filters.py
Gate checks for credit spreads, debit spreads, iron condors, and butterflies.
"""

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

MIN_CREDIT_RATIO = 0.30
MAX_DEBIT_RATIO  = 0.70
MIN_POP_CREDIT   = 0.65
MIN_POP_DEBIT    = 0.50
MAX_BID_ASK_PCT  = 0.10
MIN_OI           = 500
MIN_OI_SPX       = 200
DTE_MIN_CREDIT   = 21
DTE_MAX_CREDIT   = 45
DTE_MIN_DEBIT    = 7
DTE_MAX_DEBIT    = 21

# Butterfly-specific
MAX_BUTTERFLY_DEBIT_RATIO = 0.25  # Debit should be < 25% of wing width
MIN_BUTTERFLY_PROFIT_RATIO = 2.0  # Max profit / debit >= 2x


@dataclass
class FilterResult:
    passed:   bool
    reasons:  list[str]
    warnings: list[str]


def check_credit_spread(
    credit:        float,
    width:         float,
    pop:           float,
    bid_ask_spread: float,
    mid_price:     float,
    short_delta:   float,
    dte:           int,
    open_interest: int,
    symbol:        str,
    vix_regime:    str,
) -> FilterResult:
    reasons  = []
    warnings = []

    ratio    = credit / width if width > 0 else 0
    ba_pct   = bid_ask_spread / mid_price if mid_price > 0 else 999
    min_pop  = 0.70 if vix_regime == "elevated" else MIN_POP_CREDIT
    min_oi   = MIN_OI_SPX if symbol in ("SPX", "SPXW") else MIN_OI

    if ratio < MIN_CREDIT_RATIO:
        reasons.append(f"credit/width {ratio:.0%} < 30%")
    if pop < min_pop:
        reasons.append(f"PoP {pop:.0%} < {min_pop:.0%}")
    if ba_pct > MAX_BID_ASK_PCT:
        reasons.append(f"bid/ask {ba_pct:.0%} > 10%")
    if abs(short_delta) > 0.40:
        warnings.append(f"delta {short_delta:.2f} > 0.40 — high assignment risk")
    if dte < DTE_MIN_CREDIT or dte > DTE_MAX_CREDIT:
        reasons.append(f"DTE {dte} outside {DTE_MIN_CREDIT}–{DTE_MAX_CREDIT}")
    if open_interest < min_oi:
        warnings.append(f"OI {open_interest} < {min_oi} — liquidity concern")

    passed = len(reasons) == 0
    return FilterResult(passed=passed, reasons=reasons, warnings=warnings)


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

    ratio    = debit / width if width > 0 else 999
    ba_pct   = bid_ask_spread / mid_price if mid_price > 0 else 999
    min_oi   = MIN_OI_SPX if symbol in ("SPX", "SPXW") else MIN_OI

    if ratio > MAX_DEBIT_RATIO:
        reasons.append(f"debit/width {ratio:.0%} > 70%")
    if pop < MIN_POP_DEBIT:
        reasons.append(f"PoP {pop:.0%} < 50%")
    if ba_pct > MAX_BID_ASK_PCT:
        reasons.append(f"bid/ask {ba_pct:.0%} > 10%")
    if dte < DTE_MIN_DEBIT or dte > DTE_MAX_DEBIT:
        reasons.append(f"DTE {dte} outside {DTE_MIN_DEBIT}–{DTE_MAX_DEBIT}")
    if open_interest < min_oi:
        warnings.append(f"OI {open_interest} < {min_oi} — liquidity concern")

    passed = len(reasons) == 0
    return FilterResult(passed=passed, reasons=reasons, warnings=warnings)


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
    Butterfly filter gates.
    Key checks:
    - Debit/width < 25% (cheap entry relative to structure)
    - Max profit / debit >= 2x (reward/risk ratio)
    - Net delta near-neutral (|delta| < 0.10)
    - Liquidity acceptable
    - DTE in range (25-35 days)
    """
    reasons  = []
    warnings = []

    debit_ratio   = debit / wing_width if wing_width > 0 else 999
    profit_ratio  = max_profit / debit if debit > 0 else 0
    ba_pct        = body_bid_ask / body_mid if body_mid > 0 else 999
    min_oi        = MIN_OI_SPX if symbol in ("SPX", "SPXW") else MIN_OI

    if debit_ratio > MAX_BUTTERFLY_DEBIT_RATIO:
        reasons.append(f"debit/width {debit_ratio:.0%} > 25%")
    if profit_ratio < MIN_BUTTERFLY_PROFIT_RATIO:
        reasons.append(f"max_profit/debit {profit_ratio:.1f}x < 2x")
    if abs(net_delta) > 0.10:
        warnings.append(f"net delta {net_delta:.3f} not neutral — directional risk")
    if ba_pct > MAX_BID_ASK_PCT:
        reasons.append(f"body bid/ask {ba_pct:.0%} > 10%")
    if dte < 25 or dte > 35:
        reasons.append(f"DTE {dte} outside 25–35")
    if open_interest < min_oi:
        warnings.append(f"OI {open_interest} < {min_oi} — liquidity concern")

    passed = len(reasons) == 0
    return FilterResult(passed=passed, reasons=reasons, warnings=warnings)


def check_iron_condor(
    put_credit:      float,
    put_width:       float,
    put_delta:       float,
    put_bid_ask:     float,
    put_mid:         float,
    put_oi:          int,
    call_credit:     float,
    call_width:      float,
    call_delta:      float,
    call_bid_ask:    float,
    call_mid:        float,
    call_oi:         int,
    pop:             float,
    dte:             int,
    symbol:          str,
    vix_regime:      str,
) -> FilterResult:
    reasons  = []
    warnings = []

    put_ratio  = put_credit / put_width if put_width > 0 else 0
    call_ratio = call_credit / call_width if call_width > 0 else 0
    put_ba_pct = put_bid_ask / put_mid if put_mid > 0 else 999
    call_ba_pct = call_bid_ask / call_mid if call_mid > 0 else 999
    min_pop    = 0.70 if vix_regime == "elevated" else MIN_POP_CREDIT
    min_oi     = MIN_OI_SPX if symbol in ("SPX", "SPXW") else MIN_OI

    # Per-wing validation
    if put_ratio < MIN_CREDIT_RATIO:
        reasons.append(f"put wing credit/width {put_ratio:.0%} < 30%")
    if call_ratio < MIN_CREDIT_RATIO:
        reasons.append(f"call wing credit/width {call_ratio:.0%} < 30%")
    
    # Liquidity
    if put_ba_pct > MAX_BID_ASK_PCT:
        reasons.append(f"put wing bid/ask {put_ba_pct:.0%} > 10%")
    if call_ba_pct > MAX_BID_ASK_PCT:
        reasons.append(f"call wing bid/ask {call_ba_pct:.0%} > 10%")
    
    # Delta warnings
    if abs(put_delta) > 0.40:
        warnings.append(f"put delta {put_delta:.2f} > 0.40 — assignment risk")
    if abs(call_delta) > 0.40:
        warnings.append(f"call delta {call_delta:.2f} > 0.40 — assignment risk")
    
    # Combined PoP and DTE
    if pop < min_pop:
        reasons.append(f"combined PoP {pop:.0%} < {min_pop:.0%}")
    if dte < DTE_MIN_CREDIT or dte > DTE_MAX_CREDIT:
        reasons.append(f"DTE {dte} outside {DTE_MIN_CREDIT}–{DTE_MAX_CREDIT}")
    
    # OI
    if put_oi < min_oi or call_oi < min_oi:
        warnings.append(f"OI < {min_oi} on one or both wings")
    
    # Wing symmetry
    if abs(put_width - call_width) > 5.0:
        warnings.append(f"wings unequal: put ${put_width:.1f} vs call ${call_width:.1f}")

    passed = len(reasons) == 0
    return FilterResult(passed=passed, reasons=reasons, warnings=warnings)
