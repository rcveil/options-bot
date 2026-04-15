"""
alerts/telegram.py
Telegram bot sender and command handlers.
Commands: /check /status /help /test /scan
/scan triggers the full scan immediately — useful for testing outside 09:30 ET.
"""

import logging
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


def build_application() -> Application:
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("check",  handle_check))
    app.add_handler(CommandHandler("status", handle_status))
    app.add_handler(CommandHandler("help",   handle_help))
    app.add_handler(CommandHandler("test",   handle_test))
    app.add_handler(CommandHandler("scan",   handle_scan))
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
        f"Use /scan to trigger immediately"
    )


async def handle_help(
    update:  Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    await update.message.reply_text(
        "/check SYMBOL  —  re-evaluate latest signal\n"
        "/status        —  bot health, VIX, server time\n"
        "/test          —  test Tastytrade connection\n"
        "/scan          —  trigger full scan NOW\n"
        "/help          —  this message"
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
        # Import here to avoid circular import
        from main import run_scan
        await run_scan()
        await update.message.reply_text("✅ Scan complete.")
    except Exception as e:
        logger.error(f"/scan error: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Scan error: {e}")
