"""
alerts/formatter.py
Format signals for Telegram.
Handles vertical spreads, iron condors, and butterflies.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class SignalPayload:
    # Common
    symbol:           str
    direction:        str
    strategy:         str
    structure:        str
    vix:              float
    vix_regime:       str
    timestamp_et:     str
    expiry:           str
    dte:              int
    ivr:              float
    credit_debit:     float
    max_loss:         float
    pop:              float
    contracts:        int
    risk_dollars:     float
    risk_pct:         float
    stop_level:       float
    profit_target:    float
    stop_note:        str
    target_note:      str
    rationale:        str
    rvol:             float
    warnings:         list[str]
    
    # Vertical spreads
    sell_strike:      Optional[float] = None
    buy_strike:       Optional[float] = None
    credit_width_ratio: Optional[float] = None
    bid_ask_spread:   Optional[float] = None
    mid_price:        Optional[float] = None
    delta:            Optional[float] = None
    gamma:            Optional[float] = None
    theta:            Optional[float] = None
    vega:             Optional[float] = None
    iv:               Optional[float] = None
    open_interest:    Optional[int] = None
    
    # Iron condor
    put_sell_strike:  Optional[float] = None
    put_buy_strike:   Optional[float] = None
    put_credit:       Optional[float] = None
    put_credit_ratio: Optional[float] = None
    call_sell_strike: Optional[float] = None
    call_buy_strike:  Optional[float] = None
    call_credit:      Optional[float] = None
    call_credit_ratio: Optional[float] = None
    wing_width:       Optional[float] = None
    
    # Butterfly
    lower_strike:     Optional[float] = None
    body_strike:      Optional[float] = None
    upper_strike:     Optional[float] = None
    debit_ratio:      Optional[float] = None
    max_profit:       Optional[float] = None
    net_delta:        Optional[float] = None


def format_signal(payload: SignalPayload) -> str:
    """Format signal for Telegram with HTML."""
    
    if payload.strategy == "iron_condor":
        return _format_iron_condor(payload)
    elif payload.strategy == "long_butterfly":
        return _format_butterfly(payload)
    else:
        return _format_vertical(payload)


def _format_vertical(p: SignalPayload) -> str:
    lines = [
        f"<b>🎯 {p.symbol} {p.strategy.upper().replace('_', ' ')}</b>",
        f"",
        f"<b>Structure:</b> {p.structure.capitalize()} | {p.direction.capitalize()}",
        f"<b>Strikes:</b> Sell ${p.sell_strike} / Buy ${p.buy_strike}",
        f"<b>Expiry:</b> {p.expiry} ({p.dte} DTE)",
        f"<b>Credit/Debit:</b> ${p.credit_debit} ({p.credit_width_ratio:.0%} of width)",
        f"",
        f"<b>Risk/Reward:</b>",
        f"  Max Loss: ${p.max_loss}",
        f"  PoP: {p.pop:.0%}",
        f"  Contracts: {p.contracts}",
        f"  Risk: ${p.risk_dollars} ({p.risk_pct:.1%} account)",
        f"",
        f"<b>Greeks:</b>",
        f"  Delta: {p.delta:.3f} | IV: {p.iv:.1%}",
        f"  Theta: {p.theta:.2f} | Vega: {p.vega:.2f}",
        f"",
        f"<b>Exits:</b>",
        f"  Stop: {p.stop_note}",
        f"  Target: {p.target_note}",
        f"",
        f"<b>Market:</b>",
        f"  VIX: {p.vix:.1f} ({p.vix_regime}) | IVR: {p.ivr:.0f}",
        f"  RVOL: {p.rvol:.1f}x | Time: {p.timestamp_et}",
        f"",
        f"<b>Rationale:</b> {p.rationale}",
    ]
    
    if p.warnings:
        lines.append(f"")
        lines.append(f"<b>⚠️ Warnings:</b>")
        for w in p.warnings:
            lines.append(f"  • {w}")
    
    return "\n".join(lines)


def _format_iron_condor(p: SignalPayload) -> str:
    lines = [
        f"<b>🦅 {p.symbol} IRON CONDOR</b>",
        f"",
        f"<b>Structure:</b> Credit | Neutral",
        f"<b>Put Wing:</b> Sell ${p.put_sell_strike} / Buy ${p.put_buy_strike}",
        f"  Credit: ${p.put_credit} ({p.put_credit_ratio:.0%} of width)",
        f"<b>Call Wing:</b> Sell ${p.call_sell_strike} / Buy ${p.call_buy_strike}",
        f"  Credit: ${p.call_credit} ({p.call_credit_ratio:.0%} of width)",
        f"<b>Expiry:</b> {p.expiry} ({p.dte} DTE)",
        f"<b>Total Credit:</b> ${p.credit_debit}",
        f"",
        f"<b>Risk/Reward:</b>",
        f"  Max Loss: ${p.max_loss}",
        f"  PoP: {p.pop:.0%}",
        f"  Contracts: {p.contracts}",
        f"  Risk: ${p.risk_dollars} ({p.risk_pct:.1%} account)",
        f"",
        f"<b>Exits:</b>",
        f"  Stop: {p.stop_note}",
        f"  Target: {p.target_note}",
        f"",
        f"<b>Market:</b>",
        f"  VIX: {p.vix:.1f} ({p.vix_regime}) | IVR: {p.ivr:.0f}",
        f"  RVOL: {p.rvol:.1f}x | Time: {p.timestamp_et}",
        f"",
        f"<b>Rationale:</b> {p.rationale}",
    ]
    
    if p.warnings:
        lines.append(f"")
        lines.append(f"<b>⚠️ Warnings:</b>")
        for w in p.warnings:
            lines.append(f"  • {w}")
    
    return "\n".join(lines)


def _format_butterfly(p: SignalPayload) -> str:
    """Format butterfly signal — lottery ticket structure."""
    profit_multiple = p.max_profit / p.credit_debit if p.credit_debit > 0 else 0
    
    lines = [
        f"<b>🦋 {p.symbol} LONG BUTTERFLY</b>",
        f"",
        f"<b>Structure:</b> Debit | Neutral (Lottery Ticket)",
        f"<b>Strikes:</b> Buy ${p.lower_strike} / Sell 2×${p.body_strike} / Buy ${p.upper_strike}",
        f"<b>Wing Width:</b> ${p.wing_width}",
        f"<b>Expiry:</b> {p.expiry} ({p.dte} DTE)",
        f"",
        f"<b>Cost & Reward:</b>",
        f"  Net Debit: ${p.credit_debit} ({p.debit_ratio:.0%} of width)",
        f"  Max Profit: ${p.max_profit} ({profit_multiple:.1f}x debit)",
        f"  Max Loss: ${p.max_loss} (debit paid)",
        f"  PoP: {p.pop:.0%} (finish at body strike)",
        f"",
        f"<b>Position:</b>",
        f"  Contracts: {p.contracts}",
        f"  Risk: ${p.risk_dollars} ({p.risk_pct:.1%} account)",
        f"  Net Delta: {p.net_delta:.3f} (neutral)",
        f"",
        f"<b>Exits:</b>",
        f"  Stop: {p.stop_note}",
        f"  Target: {p.target_note}",
        f"",
        f"<b>Market:</b>",
        f"  VIX: {p.vix:.1f} ({p.vix_regime}) | IVR: {p.ivr:.0f}",
        f"  RVOL: {p.rvol:.1f}x | Time: {p.timestamp_et}",
        f"",
        f"<b>Rationale:</b> {p.rationale}",
    ]
    
    if p.warnings:
        lines.append(f"")
        lines.append(f"<b>⚠️ Warnings:</b>")
        for w in p.warnings:
            lines.append(f"  • {w}")
    
    return "\n".join(lines)


def format_vix_warning(vix: float, regime: str) -> str | None:
    if regime == "pause":
        return (
            f"<b>⛔ VIX PAUSE ALERT</b>\n\n"
            f"VIX at {vix:.1f} — market conditions too volatile.\n"
            f"Standing down until VIX < 45."
        )
    elif regime == "spike":
        return (
            f"<b>⚠️ VIX SPIKE WARNING</b>\n\n"
            f"VIX at {vix:.1f} — high volatility regime.\n"
            f"Only index signals (SPX/SPY/QQQ), 1 contract max."
        )
    elif regime == "elevated":
        return (
            f"<b>📊 VIX ELEVATED</b>\n\n"
            f"VIX at {vix:.1f} — position sizing reduced 50%."
        )
    return None
