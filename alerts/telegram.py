"""
alerts/telegram.py
Telegram bot sender and command handlers.

Commands:
  /check SYMBOL       — re-evaluate latest signal for late entry
  /status             — bot health, VIX, server time
  /help               — command list
  /test               — test Tastytrade connection
  /scan               — trigger full scan immediately
  /close SYMBOL       — log outcome for most recent signal (interactive)
  /export             — export last 30 days of signals as CSV
  /export YYYY-MM-DD  — export signals from that date onwards
  /backtest           — P&L report for all settled signals
"""

import csv
import io
import logging
from datetime import date, timedelta

from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.constants import ParseMode

from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)


async def send_signal(message: str) -> None:
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    async with bot:
        try:
            await bot.send_message(
                chat_id    = TELEGRAM_CHAT_ID,
                text       = message,
                parse_mode = ParseMode.HTML,
            )
            logger.info("Signal sent to Telegram")
        except Exception as e:
            logger.error(f"Telegram send_signal error: {e}")


async def send_warning(message: str) -> None:
    await send_signal(message)


async def send_text(message: str) -> None:
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    async with bot:
        try:
            await bot.send_message(
                chat_id = TELEGRAM_CHAT_ID,
                text    = message,
            )
        except Exception as e:
            logger.error(f"Telegram send_text error: {e}")


async def handle_backtest(
    update:  Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """/backtest — show P&L report for all settled signals.
    Auto-backfills any expired signals that have no outcome yet
    (e.g. signals from before the settlement feature was deployed).
    """
    from storage.journal import get_all_outcomes, get_all_expired_without_outcomes
    from backtest.engine import fetch_close_price, calc_pnl
    from backtest.report import format_report
    from storage.journal import log_outcome
    import asyncio

    try:
        # ── Auto-backfill missing outcomes ────────────────────────────
        pending = await get_all_expired_without_outcomes()
        if pending:
            await update.message.reply_text(
                f"⏳ Found {len(pending)} expired signal(s) with no outcome yet — "
                f"backfilling now, this may take a moment..."
            )
            settled = 0
            skipped = 0
            for sig in pending:
                symbol   = sig["symbol"]
                expiry   = sig["expiry"]
                try:
                    close = await asyncio.get_event_loop().run_in_executor(
                        None, fetch_close_price, symbol, expiry
                    )
                    if close is None:
                        skipped += 1
                        continue
                    pnl = calc_pnl(sig, close)
                    if pnl is None:
                        skipped += 1
                        continue
                    await log_outcome(
                        signal_id   = sig["id"],
                        close_price = close,
                        pnl         = pnl,
                        exit_reason = "expiry_calculated",
                    )
                    settled += 1
                except Exception as e:
                    logger.warning(f"Backfill failed for {symbol} {expiry}: {e}")
                    skipped += 1

            summary = f"✅ Backfill complete: {settled} settled"
            if skipped:
                summary += f", {skipped} skipped (butterfly / no price data)"
            await update.message.reply_text(summary)

        # ── Generate report ───────────────────────────────────────────
        rows     = await get_all_outcomes()
        messages = format_report(rows)
        for msg in messages:
            await update.message.reply_text(msg)

    except Exception as e:
        logger.error(f"handle_backtest error: {e}", exc_info=True)
        await update.message.reply_text(f"Error generating report: {e}")


def build_application() -> Application:
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("check",    handle_check))
    app.add_handler(CommandHandler("status",   handle_status))
    app.add_handler(CommandHandler("help",     handle_help))
    app.add_handler(CommandHandler("test",     handle_test))
    app.add_handler(CommandHandler("scan",     handle_scan))
    app.add_handler(CommandHandler("close",    handle_close))
    app.add_handler(CommandHandler("export",   handle_export))
    app.add_handler(CommandHandler("backtest", handle_backtest))
    return app


async def handle_check(
    update:  Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not context.args:
        await update.message.reply_text(
            "Usage: /check SYMBOL\nExample: /check NVDA"
        )
        return
    symbol = context.args[0].upper()
    await update.message.reply_text(f"Re-evaluating {symbol}...")
    try:
        from alerts.late_entry import evaluate_late_entry
        result = await evaluate_late_entry(symbol)
        await update.message.reply_text(result, parse_mode=ParseMode.HTML)
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")


async def handle_status(
    update:  Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from config.settings import TIMEZONE

    ET  = ZoneInfo(TIMEZONE)
    now = datetime.now(ET)

    try:
        from data.market import get_vix, classify_vix
        vix    = await get_vix()
        regime = classify_vix(vix)
        vix_line = f"VIX: {vix:.1f} — {regime.upper()}"
    except Exception:
        vix_line = "VIX: unavailable (market may be closed)"

    await update.message.reply_text(
        f"✅ Bot running\n"
        f"Server time: {now.strftime('%A %H:%M %Z')}\n"
        f"{vix_line}\n\n"
        f"Watchlist:\n"
        f"Semicon: MU AVGO STX AMD NVDA\n"
        f"Metals:  GLD SLV\n"
        f"Tech:    MSFT GOOGL AMZN AAPL\n"
        f"Index:   SPX SPY QQQ\n\n"
        f"Next scan: weekdays at 09:30 ET (21:30 SGT)\n"
        f"Signals fire from ~09:45 ET (ORB gate)\n"
        f"Use /scan to trigger immediately"
    )


async def handle_help(
    update:  Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    await update.message.reply_text(
        "/check SYMBOL          — re-evaluate latest signal\n"
        "/status                — bot health, VIX, server time\n"
        "/test                  — test Tastytrade connection\n"
        "/scan                  — trigger full scan NOW\n"
        "/close SYMBOL          — log trade outcome\n"
        "/export                — export last 30 days as CSV\n"
        "/export YYYY-MM-DD     — export from date as CSV\n"
        "/backtest              — P&L report for all settled signals\n"
        "/help                  — this message"
    )


async def handle_test(
    update:  Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    await update.message.reply_text("Running connection test...")
    results = []

    try:
        from data.tastytrade import get_session
        await get_session()
        results.append("✅ Session — logged in")
    except Exception as e:
        results.append(f"❌ Session — {str(e)[:80]}")

    try:
        from data.tastytrade import get_quote
        q = await get_quote("SPY")
        results.append(f"✅ Quote — SPY ${q['mid']:.2f}")
    except Exception as e:
        results.append(f"❌ Quote — {str(e)[:80]}")

    try:
        from data.market import get_vix, classify_vix
        vix    = await get_vix()
        regime = classify_vix(vix)
        results.append(f"✅ VIX — {vix:.1f} ({regime})")
    except Exception as e:
        results.append(f"❌ VIX — {str(e)[:80]}")

    try:
        from data.market import get_ivr
        ivr = await get_ivr("AAPL")
        results.append(f"✅ IVR — AAPL {ivr:.1f}")
    except Exception as e:
        results.append(f"❌ IVR — {str(e)[:80]}")

    try:
        from signals.chain import select_expiry
        from config.thresholds import DTE_CREDIT_MIN, DTE_CREDIT_MAX
        expiry = await select_expiry("AAPL", DTE_CREDIT_MIN, DTE_CREDIT_MAX)
        if expiry:
            results.append(f"✅ Chain — AAPL expiry {expiry}")
        else:
            results.append("⚠️ Chain — no expiry in DTE range")
    except Exception as e:
        results.append(f"❌ Chain — {str(e)[:80]}")

    await update.message.reply_text(
        "Connection test results\n"
        "─────────────────────\n"
        + "\n".join(results)
        + "\n─────────────────────\n"
        "Tests 2–5 may fail outside market hours."
    )


async def handle_scan(
    update:  Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """/scan — trigger the full scan immediately."""
    await update.message.reply_text(
        "🔍 Manual scan triggered. Check logs for progress.\n"
        "Signals will arrive in this chat if any pass all filters."
    )
    try:
        from main import run_scan
        await run_scan()
        await update.message.reply_text("✅ Scan complete.")
    except Exception as e:
        logger.error(f"/scan error: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Scan error: {e}")


async def handle_close(
    update:  Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    /close SYMBOL
    Shows the last 5 signals for the symbol so the user can pick
    which one to close, then prompts for a close price.

    Two-step flow:
      Step 1: /close SYMBOL  → bot lists recent signals numbered 1–5
      Step 2: /close SYMBOL N PRICE  → logs the outcome

    Examples:
      /close NVDA              → lists recent NVDA signals
      /close NVDA 1 2.40       → closes signal #1 at $2.40
    """
    if not context.args:
        await update.message.reply_text(
            "Usage:\n"
            "  /close SYMBOL           — list recent signals\n"
            "  /close SYMBOL N PRICE   — log close at price\n\n"
            "Example:\n"
            "  /close NVDA\n"
            "  /close NVDA 1 2.40"
        )
        return

    symbol = context.args[0].upper()

    # ── Step 2: user supplied a signal number and close price ──────────
    if len(context.args) == 3:
        try:
            pick        = int(context.args[1])
            close_price = float(context.args[2])
        except ValueError:
            await update.message.reply_text(
                "Could not parse arguments.\n"
                "Usage: /close SYMBOL N PRICE\n"
                "Example: /close NVDA 1 2.40"
            )
            return

        from storage.journal import get_recent_signals, log_outcome
        signals = await get_recent_signals(symbol, limit=5)

        if not signals:
            await update.message.reply_text(f"No signals found for {symbol}.")
            return

        if pick < 1 or pick > len(signals):
            await update.message.reply_text(
                f"Pick must be between 1 and {len(signals)}."
            )
            return

        sig        = signals[pick - 1]
        signal_id  = sig["id"]
        entry_credit = sig.get("credit_debit", 0) or 0

        # P&L: for credit spreads, profit = entry_credit - close_price
        # For debit spreads, profit = close_price - entry_credit
        structure = sig.get("structure", "credit")
        if structure == "credit":
            pnl = round((entry_credit - close_price) * 100, 2)
            exit_reason = (
                "profit_target" if close_price <= entry_credit * 0.5
                else "stop_loss" if close_price >= entry_credit * 2
                else "manual_close"
            )
        else:
            pnl = round((close_price - entry_credit) * 100, 2)
            exit_reason = (
                "profit_target" if close_price >= entry_credit * 1.5
                else "stop_loss" if close_price <= entry_credit * 0.5
                else "manual_close"
            )

        await log_outcome(signal_id, close_price, pnl, exit_reason)

        pnl_sign = "+" if pnl >= 0 else ""
        await update.message.reply_text(
            f"✅ Outcome logged for {symbol} signal #{pick}\n"
            f"Signal ID: {signal_id}\n"
            f"Entry credit: ${entry_credit:.2f}\n"
            f"Close price:  ${close_price:.2f}\n"
            f"P&L:          {pnl_sign}${pnl:.2f} per contract\n"
            f"Exit reason:  {exit_reason}"
        )
        return

    # ── Step 1: list recent signals for this symbol ────────────────────
    from storage.journal import get_recent_signals
    signals = await get_recent_signals(symbol, limit=5)

    if not signals:
        await update.message.reply_text(f"No signals found for {symbol}.")
        return

    lines = [f"Recent signals for {symbol} — reply with /close {symbol} N PRICE\n"]
    for i, sig in enumerate(signals, 1):
        strategy    = sig.get("strategy", "?")
        direction   = sig.get("direction", "?")
        credit      = sig.get("credit_debit", 0) or 0
        expiry      = sig.get("expiry", "?")
        created_at  = (sig.get("created_at") or "")[:16]

        # Build strike description depending on strategy
        if strategy == "iron_condor":
            ps = sig.get("put_sell_strike")
            cs = sig.get("call_sell_strike")
            strikes = f"put {ps:.0f} / call {cs:.0f}" if ps and cs else "—"
        elif sig.get("sell_strike"):
            sell = sig["sell_strike"]
            buy  = sig.get("buy_strike")
            strikes = f"{sell:.0f}/{buy:.0f}" if buy else f"{sell:.0f}"
        else:
            strikes = "—"

        lines.append(
            f"{i}. [{created_at}] {strategy} {direction} "
            f"| {strikes} exp {expiry} | credit ${credit:.2f}"
        )

    await update.message.reply_text("\n".join(lines))


async def handle_export(
    update:  Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    /export             — export last 30 days of signals as CSV
    /export YYYY-MM-DD  — export signals from that date onwards

    Sends the CSV as a file attachment directly in Telegram.
    """
    # Parse optional date argument
    if context.args:
        from_date_str = context.args[0]
        try:
            date.fromisoformat(from_date_str)   # validate format
        except ValueError:
            await update.message.reply_text(
                "Invalid date format. Use YYYY-MM-DD.\n"
                "Example: /export 2026-04-01"
            )
            return
    else:
        from_date_str = (date.today() - timedelta(days=30)).isoformat()

    await update.message.reply_text(
        f"⏳ Exporting signals from {from_date_str}..."
    )

    try:
        from storage.journal import get_signals_since
        signals = await get_signals_since(from_date_str)
    except Exception as e:
        await update.message.reply_text(f"❌ Export failed: {e}")
        return

    if not signals:
        await update.message.reply_text(
            f"No signals found from {from_date_str} onwards."
        )
        return

    # Build CSV in memory
    output   = io.StringIO()
    fieldnames = [
        "id", "created_at", "timestamp_et",
        "symbol", "strategy", "direction", "structure",
        "sell_strike", "buy_strike", "spread_width", "option_type",
        "put_sell_strike", "put_buy_strike", "put_credit", "put_credit_ratio",
        "call_sell_strike", "call_buy_strike", "call_credit", "call_credit_ratio",
        "wing_width",
        "jl_put_strike", "jl_put_credit",
        "jl_short_call_strike", "jl_long_call_strike",
        "jl_call_spread_width", "jl_call_spread_credit",
        "jl_call_spread_ratio", "jl_upside_risk_free", "jl_breakeven",
        "expiry", "dte", "credit_debit", "max_loss",
        "ivr", "vix", "vix_regime",
        "pop", "delta", "theta", "vega", "iv",
        "contracts", "risk_dollars", "vwap", "rvol", "rationale",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(signals)

    # Send as file attachment
    csv_bytes = output.getvalue().encode("utf-8")
    filename  = f"signals_{from_date_str}_to_{date.today().isoformat()}.csv"

    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    async with bot:
        await bot.send_document(
            chat_id  = update.effective_chat.id,
            document = io.BytesIO(csv_bytes),
            filename = filename,
            caption  = (
                f"📊 {len(signals)} signals from {from_date_str} "
                f"to {date.today().isoformat()}"
            ),
        )

    logger.info(f"/export: sent {len(signals)} rows from {from_date_str}")
