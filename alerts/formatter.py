"""
alerts/formatter.py
Builds Telegram message strings from signal payloads.
Supports vertical spreads and iron condors.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SignalPayload:
    # Identity
    symbol:        str
    direction:     str
    strategy:      str
    structure:     str
    vix:           float
    vix_regime:    str
    timestamp_et:  str

    # Vertical spread fields (used for all non-IC strategies)
    sell_strike:   float
    buy_strike:    float
    expiry:        str
    dte:           int
    credit_debit:  float
    max_loss:      float

    # Iron condor fields (None for vertical spreads)
    put_sell_strike:   Optional[float] = None
    put_buy_strike:    Optional[float] = None
    put_credit:        Optional[float] = None
    put_credit_ratio:  Optional[float] = None
    call_sell_strike:  Optional[float] = None
    call_buy_strike:   Optional[float] = None
    call_credit:       Optional[float] = None
    call_credit_ratio: Optional[float] = None
    wing_width:        Optional[float] = None

    # Jade lizard fields (None for other strategies)
    jl_put_strike:         Optional[float] = None
    jl_put_credit:         Optional[float] = None
    jl_short_call_strike:  Optional[float] = None
    jl_long_call_strike:   Optional[float] = None
    jl_call_spread_width:  Optional[float] = None
    jl_call_spread_credit: Optional[float] = None
    jl_call_spread_ratio:  Optional[float] = None
    jl_upside_risk_free:   Optional[bool]  = None
    jl_breakeven:          Optional[float] = None
    jl_pop_put:            Optional[float] = None
    jl_pop_call:           Optional[float] = None

    # Butterfly fields (None for other strategies)
    bf_lower_strike: Optional[float] = None
    bf_body_strike:  Optional[float] = None
    bf_upper_strike: Optional[float] = None
    bf_wing_width:   Optional[float] = None
    bf_debit_ratio:  Optional[float] = None
    bf_max_profit:   Optional[float] = None
    bf_net_delta:    Optional[float] = None

    # Threshold values
    ivr:                float = 50.0
    credit_width_ratio: float = 0.0
    bid_ask_spread:     float = 0.0
    mid_price:          float = 0.0

    # Greeks (short leg / put leg for IC)
    delta:         float = 0.0
    gamma:         float = 0.0
    theta:         float = 0.0
    vega:          float = 0.0
    iv:            float = 0.0
    open_interest: int   = 0

    # PoP and sizing
    pop:           float = 0.0
    contracts:     int   = 1
    risk_dollars:  float = 0.0
    risk_pct:      float = 0.0

    # Exits
    stop_level:    float = 0.0
    profit_target: float = 0.0
    stop_note:     str   = ""
    target_note:   str   = ""

    # Rationale
    rationale:     str         = ""
    rvol:          float       = 1.0
    warnings:      list[str]   = field(default_factory=list)


STRATEGY_LABELS = {
    "bull_put_spread":  "Bull put spread",
    "bear_call_spread": "Bear call spread",
    "bull_call_spread": "Bull call spread",
    "bear_put_spread":  "Bear put spread",
    "iron_condor":      "Iron condor",
    "jade_lizard":      "Jade lizard",
    "long_butterfly":   "Long butterfly",
}

REGIME_ICON = {
    "normal":   "🟢",
    "elevated": "🟡",
    "spike":    "🔴",
    "pause":    "⛔",
}

DIRECTION_ICON = {
    "bullish": "▲",
    "bearish": "▼",
    "neutral": "↔",
}


def _pop_bar(pop: float, width: int = 10) -> str:
    filled = round(pop * width)
    return "█" * filled + "░" * (width - filled)


def _format_vertical(p: SignalPayload, structure_tag: str,
                     credit_sign: str) -> str:
    """Format message for single vertical spread."""
    return (
        f"<b>Trade structure</b>\n"
        f"Sell  {p.sell_strike:.0f}  |  Buy  {p.buy_strike:.0f}\n"
        f"Expiry  {p.expiry}  ·  {p.dte} DTE\n"
        f"{p.structure.capitalize()}  {credit_sign}${abs(p.credit_debit):.2f}"
        f"  ·  Max loss  ${p.max_loss:.2f}\n\n"
        f"<b>Entry gates</b>\n"
        f"IVR {p.ivr:.0f}  ·  Credit/width {p.credit_width_ratio:.0%}"
        f"  ·  Bid/ask ${p.bid_ask_spread:.2f}"
    )


def _format_iron_condor(p: SignalPayload) -> str:
    """Format message for iron condor (4 strikes)."""
    return (
        f"<b>Trade structure</b>\n"
        f"Put wing:   Sell {p.put_sell_strike:.0f}  |  Buy {p.put_buy_strike:.0f}"
        f"  ·  Credit ${p.put_credit:.2f} ({p.put_credit_ratio:.0%})\n"
        f"Call wing:  Sell {p.call_sell_strike:.0f}  |  Buy {p.call_buy_strike:.0f}"
        f"  ·  Credit ${p.call_credit:.2f} ({p.call_credit_ratio:.0%})\n"
        f"Expiry  {p.expiry}  ·  {p.dte} DTE\n"
        f"Total credit  +${p.credit_debit:.2f}"
        f"  ·  Wing width  ${p.wing_width:.1f}"
        f"  ·  Max loss  ${p.max_loss:.2f}\n\n"
        f"<b>Entry gates (per wing)</b>\n"
        f"IVR {p.ivr:.0f}"
        f"  ·  Put ratio {p.put_credit_ratio:.0%}"
        f"  ·  Call ratio {p.call_credit_ratio:.0%}"
    )


def _format_jade_lizard(p: SignalPayload) -> str:
    """Format message for jade lizard (3 legs: short put + short call spread)."""
    upside_tag = "✅ Zero upside risk" if p.jl_upside_risk_free \
                 else "⚠️ Upside risk NOT eliminated"
    return (
        f"<b>Trade structure</b>\n"
        f"Sell put   {p.jl_put_strike:.0f}"
        f"  ·  Credit +${p.jl_put_credit:.2f}\n"
        f"Sell call  {p.jl_short_call_strike:.0f}"
        f"  ·  Buy call  {p.jl_long_call_strike:.0f}"
        f"  ·  Width ${p.jl_call_spread_width:.1f}"
        f"  ·  Credit +${p.jl_call_spread_credit:.2f} ({p.jl_call_spread_ratio:.0%})\n"
        f"Expiry  {p.expiry}  ·  {p.dte} DTE\n"
        f"Total credit  +${p.credit_debit:.2f}"
        f"  ·  Breakeven  ${p.jl_breakeven:.2f}\n"
        f"{upside_tag}\n\n"
        f"<b>Entry gates</b>\n"
        f"IVR {p.ivr:.0f}"
        f"  ·  Put PoP {p.jl_pop_put:.0%}"
        f"  ·  Call PoP {p.jl_pop_call:.0%}"
        f"  ·  Bid/ask ${p.bid_ask_spread:.2f}"
    )


def _format_butterfly(p: SignalPayload) -> str:
    """Format message for long butterfly (3 strikes, all calls)."""
    profit_multiple = round(p.bf_max_profit / p.credit_debit, 1)                       if p.credit_debit and p.credit_debit > 0 else 0
    return (
        f"<b>Trade structure</b>\n"
        f"Buy call  {p.bf_lower_strike:.0f}"
        f"  ·  Sell 2× call  {p.bf_body_strike:.0f}"
        f"  ·  Buy call  {p.bf_upper_strike:.0f}\n"
        f"Wing width  ${p.bf_wing_width:.1f}"
        f"  ·  Net delta  {p.bf_net_delta:+.3f}\n"
        f"Expiry  {p.expiry}  ·  {p.dte} DTE\n"
        f"Net debit  −${p.credit_debit:.2f}"
        f"  ·  Max profit  ${p.bf_max_profit:.2f}  ({profit_multiple}× debit)"
        f"  ·  Max loss  ${p.max_loss:.2f}\n\n"
        f"<b>Entry gates</b>\n"
        f"IVR {p.ivr:.0f}"
        f"  ·  Debit/width {p.bf_debit_ratio:.0%}"
        f"  ·  Body bid/ask ${p.bid_ask_spread:.2f}"
    )


def format_signal(p: SignalPayload) -> str:
    regime_tag    = f"{REGIME_ICON.get(p.vix_regime, '')} VIX {p.vix:.1f} — {p.vix_regime.upper()}"
    direction_tag = f"{DIRECTION_ICON.get(p.direction, '')} {p.direction.upper()}"
    structure_tag = "Sell to open (credit)" if p.structure == "credit" \
                    else "Buy to open (debit)"
    credit_sign   = "+" if p.credit_debit >= 0 else ""
    pop_bar       = _pop_bar(p.pop)

    if p.strategy == "iron_condor":
        trade_block = _format_iron_condor(p)
    elif p.strategy == "jade_lizard":
        trade_block = _format_jade_lizard(p)
    elif p.strategy == "long_butterfly":
        trade_block = _format_butterfly(p)
    else:
        trade_block = _format_vertical(p, structure_tag, credit_sign)

    warning_block = ""
    if p.warnings:
        warning_block = "\n\n" + "\n".join(f"⚠️ {w}" for w in p.warnings)

    if p.strategy == "long_butterfly":
        pop_line = "N/A (max profit if stock pins body strike at expiry)"
    else:
        pop_line = f"{pop_bar}  {p.pop:.0%}"

    return f"""{regime_tag}
{p.timestamp_et}

<b>{p.symbol}  {direction_tag}</b>
{STRATEGY_LABELS.get(p.strategy, p.strategy)}  ·  {structure_tag}

{trade_block}

<b>Greeks (short leg)</b>
Δ {p.delta:+.3f}  Γ {p.gamma:.4f}  Θ {p.theta:+.3f}  V {p.vega:.3f}
IV {p.iv:.1%}  ·  OI {p.open_interest:,}

<b>Probability of profit</b>
{pop_line}

<b>Suggested size</b>
{p.contracts} contract{'s' if p.contracts > 1 else ''}  ·  Risk ${p.risk_dollars:,.0f} ({p.risk_pct:.1%} of account)

<b>Exit plan</b>
Stop:    {p.stop_note}
Target:  {p.target_note}

<b>Rationale</b>
{p.rationale}
RVOL {p.rvol:.1f}x at signal time.{warning_block}""".strip()


def format_vix_warning(vix: float, regime: str) -> str:
    if regime == "pause":
        return (
            f"⛔ <b>VIX SPIKE — STANDING DOWN</b>\n\n"
            f"VIX is at {vix:.1f} (above pause threshold of 45).\n"
            f"No signals will fire today. Protect capital.\n"
            f"Bot resumes scanning next session."
        )
    if regime == "spike":
        return (
            f"🔴 <b>VIX SPIKE — INDEX ONLY MODE</b>\n\n"
            f"VIX is at {vix:.1f}. Single-stock signals suppressed.\n"
            f"Only SPX / SPY / QQQ alerts active.\n"
            f"Max 1 contract per trade. Credit spreads only."
        )
    if regime == "elevated":
        return (
            f"🟡 <b>VIX ELEVATED — REDUCED SIZE MODE</b>\n\n"
            f"VIX is at {vix:.1f}. All signals active but size halved.\n"
            f"Min PoP raised to 70%. Use limit orders at mid."
        )
    return ""


def format_late_entry(
    symbol:           str,
    strategy:         str,
    original_time:    str,
    original_credit:  float,
    current_credit:   float,
    current_ratio:    float,
    current_pop:      float,
    current_ivr:      float,
    price_vs_vwap:    str,
    verdict:          str,
    advice:           str,
    # IC-specific (optional)
    put_ratio:        Optional[float] = None,
    call_ratio:       Optional[float] = None,
    # Jade lizard-specific (optional)
    jl_put_credit:    Optional[float] = None,
    jl_call_credit:   Optional[float] = None,
    jl_upside_free:   Optional[bool]  = None,
) -> str:
    verdict_map = {
        "valid":    "✅ Still valid — enter at current market",
        "marginal": "⚠️ Marginal — 1 contract only",
        "expired":  "❌ Setup expired — do not enter",
    }

    wing_ratios = ""
    if put_ratio is not None and call_ratio is not None:
        wing_ratios = (
            f"Put wing ratio: {put_ratio:.0%}  ·  "
            f"Call wing ratio: {call_ratio:.0%}\n"
        )

    jl_block = ""
    if jl_put_credit is not None and jl_call_credit is not None:
        upside_tag = "✅ Zero upside risk intact" if jl_upside_free \
                     else "⚠️ Upside risk no longer eliminated"
        jl_block = (
            f"Put credit now:   ${jl_put_credit:.2f}\n"
            f"Call spread now:  ${jl_call_credit:.2f}\n"
            f"{upside_tag}\n"
        )

    return f"""🕐 <b>Late entry check — {symbol}</b>
Original alert: {original_time}
Strategy: {STRATEGY_LABELS.get(strategy, strategy)}

<b>Original vs now</b>
Credit then:  ${original_credit:.2f}
Credit now:   ${current_credit:.2f}
{jl_block}{wing_ratios}Credit/width: {current_ratio:.0%}  ·  PoP: {current_pop:.0%}
IVR: {current_ivr:.0f}  ·  Price vs VWAP: {price_vs_vwap}

<b>Verdict</b>
{verdict_map.get(verdict, verdict)}

{advice}""".strip()
