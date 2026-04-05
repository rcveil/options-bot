"""
alerts/telegram.py
Telegram bot sender and command handler.
Commands: /check SYMBOL  /status  /help
"""

import logging
from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.constants import ParseMode

from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)

_bot: Bot | None = None


async def get_bot() -> Bot:
    global _bot
    if _bot is None:
        _bot = Bot(token=TELEGRAM_BOT_TOKEN)
    return _bot


async def send_signal(message: str) -> None:
    bot = await get_bot()
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
    bot = await get_bot()
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
    app.add_handler(CommandHandler("test", handle_test))
    return app


async def handle_check(
    update:  Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """/check SYMBOL — re-evaluate latest signal for a ticker."""
    if not context.args:
        await update.message.reply_text(
            "Usage: /check SYMBOL\nExample: /check NVDA"
        )
        return

    symbol = context.args[0].upper()
    await update.message.reply_text(
        f"Re-evaluating latest signal for {symbol}..."
    )

    from alerts.late_entry import evaluate_late_entry
    result = await evaluate_late_entry(symbol)
    await update.message.reply_text(result, parse_mode=ParseMode.HTML)


async def handle_status(
    update:  Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """/status — show bot health and current VIX regime."""
    try:
        from data.market import get_vix, classify_vix
        vix    = await get_vix()
        regime = classify_vix(vix)
        vix_line = f"VIX: {vix:.1f} — {regime.upper()}"
    except Exception as e:
        vix_line = f"VIX: unavailable (market may be closed)"

    await update.message.reply_text(
        f"✅ Bot running\n"
        f"{vix_line}\n\n"
        f"Watchlist:\n"
        f"Semicon: MU AVGO STX AMD NVDA\n"
        f"Metals:  GLD SLV\n"
        f"Tech:    MSFT GOOGL AMZN AAPL\n"
        f"Index:   SPX SPY QQQ\n\n"
        f"Next scan: weekdays at 09:30 ET (21:30 SGT)"
    )


async def handle_help(
    update:  Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """/help — list available commands."""
    await update.message.reply_text(
        "/check SYMBOL — re-evaluate latest signal\n"
        "/status       — bot health and VIX level\n"
        "/help         — this message"
    )

async def handle_test(
    update:  Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """/test — verify Tastytrade connection and live data."""
    await update.message.reply_text("Running connection test, please wait...")

    results = []

    # Test 1: Session
    try:
        from data.tastytrade import get_session
        await get_session()
        results.append("✅ Session — logged in successfully")
    except Exception as e:
        results.append(f"❌ Session — {str(e)[:60]}")

    # Test 2: Live quote
    try:
        from data.tastytrade import get_quote
        quote = await get_quote("SPY")
        results.append(f"✅ Quote — SPY mid ${quote['mid']:.2f}")
    except Exception as e:
        results.append(f"❌ Quote — {str(e)[:60]}")

    # Test 3: VIX
    try:
        from data.market import get_vix, classify_vix
        vix    = await get_vix()
        regime = classify_vix(vix)
        results.append(f"✅ VIX — {vix:.1f} ({regime})")
    except Exception as e:
        results.append(f"❌ VIX — {str(e)[:60]}")

    # Test 4: IVR
    try:
        from data.market import get_ivr
        ivr = await get_ivr("AAPL")
        results.append(f"✅ IVR — AAPL {ivr:.1f}")
    except Exception as e:
        results.append(f"❌ IVR — {str(e)[:60]}")

    # Test 5: Option chain
    try:
        from signals.chain import select_expiry
        from config.thresholds import DTE_CREDIT_MIN, DTE_CREDIT_MAX
        expiry = await select_expiry("AAPL", DTE_CREDIT_MIN, DTE_CREDIT_MAX)
        if expiry:
            results.append(f"✅ Chain — AAPL expiry {expiry}")
        else:
            results.append(f"⚠️ Chain — no expiry in DTE range (normal if market closed)")
    except Exception as e:
        results.append(f"❌ Chain — {str(e)[:60]}")

    summary = (
        "Connection test results\n"
        "─────────────────────\n"
        + "\n".join(results)
        + "\n─────────────────────\n"
        "Tests 2–5 may fail outside market hours.\n"
        "Test 1 is the critical one."
    )
    await update.message.reply_text(summary)
