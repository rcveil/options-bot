"""
signals/strategy.py
Strategy selection, Black-Scholes PoP, position sizing.
"""

import math
import logging
from dataclasses import dataclass
from scipy.stats import norm

logger = logging.getLogger(__name__)


@dataclass
class StrategyDecision:
    strategy:   str
    structure:  str
    direction:  str
    dte_target: tuple[int, int]
    rationale:  str


def select_strategy(
    direction: str | None,
    ivr:       float,
    vix_regime: str,
) -> StrategyDecision:
    """
    Route to strategy based on direction + IVR.
    
    New logic:
    - IVR 30-50 + neutral → long butterfly (lottery ticket)
    - IVR > 50 + neutral → iron condor (premium collection)
    - All other routing unchanged
    """
    # No directional bias
    if direction is None:
        if ivr > 50:
            return StrategyDecision(
                strategy   = "iron_condor",
                structure  = "credit",
                direction  = "neutral",
                dte_target = (21, 45),
                rationale  = f"No directional bias. IVR {ivr:.0f} > 50 — selling premium both sides.",
            )
        elif ivr >= 30:
            return StrategyDecision(
                strategy   = "long_butterfly",
                structure  = "debit",
                direction  = "neutral",
                dte_target = (25, 35),
                rationale  = f"No directional bias. IVR {ivr:.0f} in 30-50 range — butterfly lottery ticket.",
            )
        else:
            return StrategyDecision(
                strategy   = "no_trade",
                structure  = "none",
                direction  = "neutral",
                dte_target = (0, 0),
                rationale  = f"No directional bias. IVR {ivr:.0f} < 30 — low IV, no neutral trade.",
            )
    
    # Bullish bias
    if direction == "bullish":
        if ivr > 50:
            return StrategyDecision(
                strategy   = "bull_put_spread",
                structure  = "credit",
                direction  = "bullish",
                dte_target = (21, 45),
                rationale  = f"Bullish trend. IVR {ivr:.0f} > 50 — sell puts, collect premium.",
            )
        elif ivr < 30:
            return StrategyDecision(
                strategy   = "bull_call_spread",
                structure  = "debit",
                direction  = "bullish",
                dte_target = (7, 21),
                rationale  = f"Bullish trend. IVR {ivr:.0f} < 30 — buy calls, low IV entry.",
            )
        else:
            return StrategyDecision(
                strategy   = "bull_put_spread",
                structure  = "credit",
                direction  = "bullish",
                dte_target = (21, 45),
                rationale  = f"Bullish trend. IVR {ivr:.0f} in mid-range — credit spread.",
            )
    
    # Bearish bias
    if direction == "bearish":
        if ivr > 50:
            return StrategyDecision(
                strategy   = "bear_call_spread",
                structure  = "credit",
                direction  = "bearish",
                dte_target = (21, 45),
                rationale  = f"Bearish trend. IVR {ivr:.0f} > 50 — sell calls, collect premium.",
            )
        elif ivr < 30:
            return StrategyDecision(
                strategy   = "bear_put_spread",
                structure  = "debit",
                direction  = "bearish",
                dte_target = (7, 21),
                rationale  = f"Bearish trend. IVR {ivr:.0f} < 30 — buy puts, low IV entry.",
            )
        else:
            return StrategyDecision(
                strategy   = "bear_call_spread",
                structure  = "credit",
                direction  = "bearish",
                dte_target = (21, 45),
                rationale  = f"Bearish trend. IVR {ivr:.0f} in mid-range — credit spread.",
            )
    
    # Fallback
    return StrategyDecision(
        strategy   = "no_trade",
        structure  = "none",
        direction  = "unknown",
        dte_target = (0, 0),
        rationale  = "Unknown direction or IVR — no trade.",
    )


def compute_pop(
    current_price: float,
    strike:        float,
    dte:           int,
    iv:            float,
    option_type:   str,
) -> float:
    """
    Black-Scholes probability of profit (OTM at expiry).
    Returns value 0-1.
    """
    if dte <= 0 or iv <= 0:
        return 0.0
    
    t     = dte / 365.0
    sigma = iv
    s     = current_price
    k     = strike
    
    try:
        d1 = (math.log(s / k) + (0.5 * sigma ** 2) * t) / (sigma * math.sqrt(t))
        
        if option_type == "P":
            pop = norm.cdf(-d1)
        else:
            pop = norm.cdf(d1)
        
        return round(pop, 4)
    except (ValueError, ZeroDivisionError):
        return 0.0


def compute_position_size(
    account_size: float,
    max_loss:     float,
    vix_regime:   str,
) -> dict:
    """
    Position sizing based on VIX regime.
    
    Returns contracts, risk_dollars, risk_pct.
    """
    if vix_regime == "normal":
        target_risk_pct = 0.05
    elif vix_regime == "elevated":
        target_risk_pct = 0.025
    elif vix_regime == "spike":
        target_risk_pct = 0.02
    else:
        target_risk_pct = 0.02
    
    target_risk_dollars = account_size * target_risk_pct
    contracts           = max(1, min(3, int(target_risk_dollars / max_loss)))
    actual_risk_dollars = contracts * max_loss
    actual_risk_pct     = actual_risk_dollars / account_size
    
    return {
        "contracts":    contracts,
        "risk_dollars": round(actual_risk_dollars, 2),
        "risk_pct":     round(actual_risk_pct, 4),
    }
