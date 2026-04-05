"""
alerts/formatter.py
Builds Telegram message strings from signal payloads.
Uses HTML parse mode for bold/formatting in Telegram.
"""

from dataclasses import dataclass, field


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

    # Trade structure
    sell_strike:   float
    buy_strike:    float
    expiry:        str
    dte:           int
    credit_debit:  float
    max_loss:      float

    # Threshold values
    ivr:                float
    credit_width_ratio: float
    bid_ask_spread:     float
    mid_price:          float

    # Greeks
    delta:         float
    gamma:         float
    theta:         float
    vega:          float
    iv:            float
    open_interest: int

    # PoP and sizing
    pop:           float
    contracts:     int
    risk_dollars:  float
    risk_pct:      float

    # Exits
    stop_level:    float
    profit_target: float
    stop_note:     str
    target_note:   str

    # Rationale
    rationale:     str
    rvol:          float
    warnings:      list[str] = field(default_factory=list)


STRATEGY_LABELS = {
    "bull_put_spread":  "Bull put spread",
    "bear_call_spread": "Bear call spread",
    "bull_call_spread": "Bull call spread",
    "bear_put_spread":  "Bear put spread",
    "iron_condor":      "Iron condor",
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


def format_signal(p: SignalPayload) -> str:
    regime_tag    = f"{REGIME_ICON.get(p.vix_regime, '')} VIX {p.vix:.1f} — {p.vix_regime.upper()}"
    direction_tag = f"{DIRECTION_ICON.get(p.direction, '')} {p.direction.upper()}"
    structure_tag = "Sell to open (credit)" if p.structure == "credit" \
                    else "Buy to open (debit)"
    credit_sign   = "+" if p.credit_debit >= 0 else ""
    pop_bar       = _pop_bar(p.pop)

    warning_block = ""
    if p.warnings:
        warning_block = "\n\n" + "\n".join(f"⚠️ {w}" for w in p.warnings)

    return f"""{regime_tag}
{p.timestamp_et}

<b>{p.symbol}  {direction_tag}</b>
{STRATEGY_LABELS.get(p.strategy, p.strategy)}  ·  {structure_tag}

<b>Trade structure</b>
Sell  {p.sell_strike:.0f}  |  Buy  {p.buy_strike:.0f}
Expiry  {p.expiry}  ·  {p.dte} DTE
{p.structure.capitalize()}  {credit_sign}${abs(p.credit_debit):.2f}  ·  Max loss  ${p.max_loss:.2f}

<b>Entry gates</b>
IVR {p.ivr:.0f}  ·  Credit/width {p.credit_width_ratio:.0%}  ·  Bid/ask ${p.bid_ask_spread:.2f}

<b>Greeks</b>
Δ {p.delta:+.3f}  Γ {p.gamma:.4f}  Θ {p.theta:+.3f}  V {p.vega:.3f}
IV {p.iv:.1%}  ·  OI {p.open_interest:,}

<b>Probability of profit</b>
{pop_bar}  {p.pop:.0%}

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
) -> str:
    verdict_map = {
        "valid":    "✅ Still valid — enter at current market",
        "marginal": "⚠️ Marginal — 1 contract only",
        "expired":  "❌ Setup expired — do not enter",
    }
    return f"""🕐 <b>Late entry check — {symbol}</b>
Original alert: {original_time}
Strategy: {STRATEGY_LABELS.get(strategy, strategy)}

<b>Original vs now</b>
Credit then:  ${original_credit:.2f}
Credit now:   ${current_credit:.2f}
Credit/width: {current_ratio:.0%}  ·  PoP: {current_pop:.0%}
IVR: {current_ivr:.0f}  ·  Price vs VWAP: {price_vs_vwap}

<b>Verdict</b>
{verdict_map.get(verdict, verdict)}

{advice}""".strip()
