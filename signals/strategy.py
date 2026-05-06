"""
signals/strategy.py
Strategy selector, Black-Scholes PoP calculator, position sizer.
"""

import math
from dataclasses import dataclass
from typing import Literal
from scipy.stats import norm

from config.thresholds import (
    IVR_SELL_MIN, IVR_BUY_MAX,
    DTE_CREDIT_MIN, DTE_CREDIT_MAX,
    DTE_DEBIT_MIN,  DTE_DEBIT_MAX,
)

StrategyType = Literal[
    "bull_put_spread",
    "bear_call_spread",
    "iron_condor",
    "jade_lizard",
    "bull_call_spread",
    "bear_put_spread",
    "no_trade",
]


@dataclass
class StrategyDecision:
    strategy:    StrategyType
    structure:   str           # "credit" or "debit"
    direction:   str           # "bullish" / "bearish" / "neutral"
    dte_target:  tuple[int, int]
    rationale:   str


def select_strategy(
    direction:  str | None,
    ivr:        float,
    vix_regime: str,
) -> StrategyDecision:
    """
    Core decision tree: IVR × direction → strategy.
    Rule: long leg must not cost more than short leg yields.
    Enforced via credit/width ratio gate in filters.py.
    """

    # Neutral + high IV → iron condor
    if direction is None and ivr >= IVR_SELL_MIN:
        return StrategyDecision(
            strategy   = "iron_condor",
            structure  = "credit",
            direction  = "neutral",
            dte_target = (DTE_CREDIT_MIN, DTE_CREDIT_MAX),
            rationale  = (
                f"No directional bias. IVR {ivr:.0f} is elevated — "
                f"selling premium on both sides via iron condor. "
                f"Theta works from day one."
            ),
        )

    # Neutral + low IV → skip
    if direction is None:
        return StrategyDecision(
            strategy   = "no_trade",
            structure  = "",
            direction  = "neutral",
            dte_target = (0, 0),
            rationale  = (
                f"No directional bias and IVR {ivr:.0f} is low. "
                f"Insufficient premium to justify a neutral position."
            ),
        )

    # High IV → sell premium
    if ivr >= IVR_SELL_MIN:
        if direction == "bullish":
            return StrategyDecision(
                strategy   = "jade_lizard",
                structure  = "credit",
                direction  = "bullish",
                dte_target = (DTE_CREDIT_MIN, DTE_CREDIT_MAX),
                rationale  = (
                    f"Bullish bias with IVR {ivr:.0f} — premium is rich. "
                    f"Jade lizard: short OTM put + short call spread. "
                    f"Volatility skew makes the put richer than the call. "
                    f"Zero upside risk when total credit > call spread width."
                ),
            )
        return StrategyDecision(
            strategy   = "bear_call_spread",
            structure  = "credit",
            direction  = "bearish",
            dte_target = (DTE_CREDIT_MIN, DTE_CREDIT_MAX),
            rationale  = (
                f"Bearish bias with IVR {ivr:.0f} — premium is rich. "
                f"Selling an OTM call spread above resistance collects "
                f"credit while giving the stock room to move."
            ),
        )

    # Low IV → buy premium
    if ivr <= IVR_BUY_MAX:
        if direction == "bullish":
            return StrategyDecision(
                strategy   = "bull_call_spread",
                structure  = "debit",
                direction  = "bullish",
                dte_target = (DTE_DEBIT_MIN, DTE_DEBIT_MAX),
                rationale  = (
                    f"Bullish bias with IVR {ivr:.0f} — IV is cheap. "
                    f"Buying a call debit spread captures upside without "
                    f"overpaying for the long leg."
                ),
            )
        return StrategyDecision(
            strategy   = "bear_put_spread",
            structure  = "debit",
            direction  = "bearish",
            dte_target = (DTE_DEBIT_MIN, DTE_DEBIT_MAX),
            rationale  = (
                f"Bearish bias with IVR {ivr:.0f} — IV is cheap. "
                f"Buying a put debit spread captures the downside move."
            ),
        )

    # Mid IV (30–50): credit only if signal passes 30% gate
    if direction == "bullish":
        return StrategyDecision(
            strategy   = "bull_put_spread",
            structure  = "credit",
            direction  = "bullish",
            dte_target = (DTE_CREDIT_MIN, DTE_CREDIT_MAX),
            rationale  = (
                f"Bullish bias. IVR {ivr:.0f} is mid-range — credit spread "
                f"only if credit/width >= 30% gate is met, otherwise skip."
            ),
        )
    return StrategyDecision(
        strategy   = "bear_call_spread",
        structure  = "credit",
        direction  = "bearish",
        dte_target = (DTE_CREDIT_MIN, DTE_CREDIT_MAX),
        rationale  = (
            f"Bearish bias. IVR {ivr:.0f} is mid-range — credit spread "
            f"only if credit/width >= 30% gate is met, otherwise skip."
        ),
    )


def compute_pop(
    underlying_price: float,
    strike:           float,
    dte:              int,
    iv:               float,
    option_type:      str,   # "P" or "C"
) -> float:
    """
    Black-Scholes probability of expiring OTM (profit for seller).
    Returns 0.0–1.0.
    """
    if iv <= 0 or dte <= 0:
        return 0.0
    t  = dte / 365
    d2 = (
        math.log(underlying_price / strike)
        + (-0.5 * iv ** 2) * t
    ) / (iv * math.sqrt(t))
    if option_type == "P":
        return float(norm.cdf(d2))    # prob price stays above strike
    return float(norm.cdf(-d2))       # prob price stays below strike


def compute_position_size(
    account_size: float,
    max_loss:     float,
    vix_regime:   str,
    risk_pct_min: float = 0.02,
    risk_pct_max: float = 0.05,
) -> dict:
    """
    Calculate position size in contracts.
    Halves size in elevated/spike regimes.
    Hard cap: 3 contracts.
    """
    target_risk = account_size * risk_pct_max
    if vix_regime in ("elevated", "spike"):
        target_risk *= 0.5

    if max_loss <= 0:
        return {"contracts": 1, "risk_dollars": 0.0, "risk_pct": 0.0}

    contracts   = max(1, int(target_risk // max_loss))
    contracts   = min(contracts, 3)
    actual_risk = contracts * max_loss

    return {
        "contracts":    contracts,
        "risk_dollars": round(actual_risk, 2),
        "risk_pct":     round(actual_risk / account_size, 4),
    }
