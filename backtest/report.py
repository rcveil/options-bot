"""
backtest/report.py
Format backtest results from the outcomes table into Telegram-ready text.

format_report() takes a list of outcome rows (from get_all_outcomes())
and returns a list of message strings, each ≤ 3800 chars to stay safely
under Telegram's 4096-character limit.
"""

from collections import defaultdict

TELEGRAM_LIMIT = 3800


def format_report(rows: list[dict]) -> list[str]:
    """
    Build a backtest performance report from outcome rows.

    Each row must have: symbol, strategy, direction, structure,
    vix_regime, pnl, exit_reason, closed_at (from get_all_outcomes()).
    """
    if not rows:
        return ["No settled signals yet — outcomes are calculated at 16:00 ET each trading day."]

    # ── Aggregate stats ────────────────────────────────────────────────
    total_pnl  = 0.0
    wins       = 0
    losses     = 0
    manual_ct  = 0
    skip_ct    = 0

    by_strategy: dict[str, dict] = defaultdict(lambda: {"pnl": 0.0, "w": 0, "l": 0})
    by_regime:   dict[str, dict] = defaultdict(lambda: {"pnl": 0.0, "w": 0, "l": 0})

    win_pnls  = []
    loss_pnls = []

    dates = []

    for r in rows:
        pnl    = r.get("pnl")
        if pnl is None:
            skip_ct += 1
            continue

        pnl      = float(pnl)
        strategy = r.get("strategy", "unknown")
        regime   = r.get("vix_regime") or "unknown"
        source   = r.get("exit_reason", "")

        total_pnl += pnl
        if pnl >= 0:
            wins += 1
            win_pnls.append(pnl)
            by_strategy[strategy]["w"] += 1
            by_regime[regime]["w"]     += 1
        else:
            losses += 1
            loss_pnls.append(pnl)
            by_strategy[strategy]["l"] += 1
            by_regime[regime]["l"]     += 1

        by_strategy[strategy]["pnl"] += pnl
        by_regime[regime]["pnl"]     += pnl

        if source != "expiry_calculated":
            manual_ct += 1

        closed = r.get("closed_at") or r.get("created_at") or ""
        if closed:
            dates.append(closed[:10])

    total_trades = wins + losses
    if total_trades == 0:
        return ["No settled signals with P&L data yet."]

    win_rate   = wins / total_trades
    avg_pnl    = total_pnl / total_trades
    avg_winner = sum(win_pnls)  / len(win_pnls)  if win_pnls  else 0.0
    avg_loser  = sum(loss_pnls) / len(loss_pnls) if loss_pnls else 0.0

    date_range = ""
    if dates:
        date_range = f"{min(dates)} → {max(dates)}"

    def pnl_str(v: float) -> str:
        return f"+${v:,.2f}" if v >= 0 else f"-${abs(v):,.2f}"

    def pct(v: float) -> str:
        return f"{v:.0%}"

    # ── Build message sections ─────────────────────────────────────────
    lines: list[str] = []

    # Header
    lines.append("📊 Backtest Report")
    if date_range:
        lines.append(f"{date_range} | {total_trades} signals")
    else:
        lines.append(f"{total_trades} signals settled")
    lines.append("")

    # Overall
    lines.append(f"Win Rate:   {pct(win_rate)} ({wins}W / {losses}L)")
    lines.append(f"Total P&L:  {pnl_str(total_pnl)}")
    lines.append(f"Avg/trade:  {pnl_str(avg_pnl)}")
    lines.append(f"Avg winner: {pnl_str(avg_winner)}  |  Avg loser: {pnl_str(avg_loser)}")
    lines.append("")

    # By strategy
    lines.append("By Strategy:")
    for strat, d in sorted(by_strategy.items(), key=lambda x: -x[1]["pnl"]):
        n   = d["w"] + d["l"]
        wr  = d["w"] / n if n else 0
        lines.append(
            f"  {strat:<22} {pct(wr):>4}  ({n:>2})  {pnl_str(d['pnl'])}"
        )
    lines.append("")

    # By VIX regime
    lines.append("By VIX Regime:")
    for regime, d in sorted(by_regime.items(), key=lambda x: -x[1]["pnl"]):
        n  = d["w"] + d["l"]
        wr = d["w"] / n if n else 0
        lines.append(
            f"  {regime:<10} {pct(wr):>4}  ({n:>2})  {pnl_str(d['pnl'])}"
        )
    lines.append("")

    # Footer
    footer_parts = []
    if skip_ct:
        footer_parts.append(f"{skip_ct} skipped (butterfly / no price data)")
    if manual_ct:
        footer_parts.append(f"{manual_ct} manual outcome{'s' if manual_ct != 1 else ''}")
    if footer_parts:
        lines.append(" | ".join(footer_parts))

    # ── Split into Telegram-safe chunks ───────────────────────────────
    messages: list[str] = []
    current_chunk: list[str] = []
    current_len = 0

    for line in lines:
        line_len = len(line) + 1  # +1 for newline
        if current_len + line_len > TELEGRAM_LIMIT and current_chunk:
            messages.append("\n".join(current_chunk))
            current_chunk = [line]
            current_len   = line_len
        else:
            current_chunk.append(line)
            current_len += line_len

    if current_chunk:
        messages.append("\n".join(current_chunk))

    return messages
