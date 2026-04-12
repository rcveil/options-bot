"""
main.py
Scheduler and main scan loop.
Fires every weekday at 09:30 ET.
"""

import asyncio
import logging
import schedule
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import Update

from config.settings import ACCOUNT_SIZE, TIMEZONE
from config.watchlist import ALL_SYMBOLS, INDEX_ONLY
from config.thresholds import (
    DTE_CREDIT_MIN, DTE_CREDIT_MAX,
    DTE_DEBIT_MIN,  DTE_DEBIT_MAX,
)
from data.market import get_vix, classify_vix, get_ivr
from data.tastytrade import get_quote
from data.candles import fetch_intraday_bars, fetch_avg_volume
from signals.indicators import run_all, compute_rvol
from signals.strategy import (
    select_strategy, compute_pop, compute_position_size,
)
from signals.chain import build_spread
from signals.filters import check_credit_spread, check_debit_spread
from signals.sizing import credit_exits, debit_exits
from alerts.formatter import SignalPayload, format_signal, format_vix_warning
from alerts.telegram import send_signal, send_warning, build_application
from storage.journal import init_db, log_signal

logging.basicConfig(
    level  = logging.INFO,
    format = "%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger("main")

ET = ZoneInfo(TIMEZONE)


async def scan_ticker(symbol: str, vix: float, regime: str) -> None:
    """Full signal pipeline for one ticker."""
    try:
        # 1. Fetch 1-min bars
        df = await fetch_intraday_bars(symbol)
        if df.empty or len(df) < 5:
            logger.info(f"{symbol}: insufficient bar data, skipping")
            return

        # 2. Average volume for RVOL
        avg_vol = await fetch_avg_volume(symbol)
        rvol    = compute_rvol(df, avg_vol)

        # 3. Run indicators
        ind = run_all(df)
        if not ind:
            logger.info(f"{symbol}: indicators empty, skipping")
            return

        direction = ind["direction"]
        price     = ind["price"]
        vwap      = ind["vwap"]

        logger.info(
            f"{symbol}: price={price:.2f} vwap={vwap:.2f} "
            f"direction={direction} rvol={rvol}"
        )

        # 4. IVR
        ivr = await get_ivr(symbol)

        # 5. Strategy selection
        decision = select_strategy(direction, ivr, regime)
        if decision.strategy == "no_trade":
            logger.info(f"{symbol}: no_trade — {decision.rationale[:80]}")
            return

        dte_min, dte_max = decision.dte_target

        # 6. Find best spread via option chain
        # chain.py returns net_credit (short mid - long mid) and credit_ratio
        spread = await build_spread(
            symbol           = symbol,
            structure        = decision.structure,
            direction        = decision.direction,
            underlying_price = price,
            dte_min          = dte_min,
            dte_max          = dte_max,
        )
        if spread is None:
            logger.info(f"{symbol}: no valid spread found")
            return

        sell_strike  = spread["sell_strike"]
        buy_strike   = spread["buy_strike"]
        spread_width = spread["spread_width"]
        expiry       = spread["expiry"]
        dte          = spread["dte"]
        greeks       = spread["greeks"]       # short leg greeks
        option_type  = "P" if "put" in decision.strategy else "C"

        # Use net_credit from chain (short mid - long mid), not just short mid
        net_credit   = spread["net_credit"]
        credit_ratio = spread["credit_ratio"]
        max_loss     = spread["max_loss"]     # spread_width - net_credit
        ba_spread    = greeks["ask"] - greeks["bid"]

        # 7. PoP — based on short strike
        pop = compute_pop(
            price, sell_strike, dte, greeks["iv"], option_type
        )

        # 8. Filter gates
        if decision.structure == "credit":
            result = check_credit_spread(
                credit         = net_credit,
                width          = spread_width,
                pop            = pop,
                bid_ask_spread = ba_spread,
                mid_price      = greeks["mid"],
                short_delta    = greeks["delta"],
                dte            = dte,
                open_interest  = greeks.get("oi", 999),
                symbol         = symbol,
                vix_regime     = regime,
            )
        else:
            result = check_debit_spread(
                debit          = net_credit,
                width          = spread_width,
                pop            = pop,
                bid_ask_spread = ba_spread,
                mid_price      = greeks["mid"],
                long_delta     = greeks["delta"],
                dte            = dte,
                open_interest  = greeks.get("oi", 999),
                symbol         = symbol,
                vix_regime     = regime,
            )

        if not result.passed:
            logger.info(
                f"{symbol}: filtered — {'; '.join(result.reasons)}"
            )
            return

        # 9. Position sizing — based on max loss per contract
        sizing = compute_position_size(
            account_size = ACCOUNT_SIZE,
            max_loss     = max_loss * 100,   # per contract (100 multiplier)
            vix_regime   = regime,
        )

        # 10. Exit levels — based on net credit received
        exits = credit_exits(net_credit) if decision.structure == "credit" \
                else debit_exits(net_credit)

        # 11. Enrich rationale with indicator data
        rationale = (
            f"{decision.rationale} "
            f"Price {'above' if price > vwap else 'below'} VWAP "
            f"({price:.2f} vs {vwap:.2f}). "
            f"RSI {ind['rsi']:.0f}. "
            f"MACD histogram {'positive' if ind['macd_hist'] > 0 else 'negative'}. "
            f"ORB {'broken' if (price > ind['orb_high'] or price < ind['orb_low']) else 'intact'}."
        )

        # 12. Build and send signal
        now_et = datetime.now(ET).strftime("%H:%M ET")

        payload = SignalPayload(
            symbol             = symbol,
            direction          = decision.direction,
            strategy           = decision.strategy,
            structure          = decision.structure,
            vix                = vix,
            vix_regime         = regime,
            timestamp_et       = now_et,
            sell_strike        = sell_strike,
            buy_strike         = buy_strike,
            expiry             = expiry,
            dte                = dte,
            credit_debit       = net_credit,
            max_loss           = max_loss,
            ivr                = ivr,
            credit_width_ratio = credit_ratio,
            bid_ask_spread     = ba_spread,
            mid_price          = greeks["mid"],
            delta              = greeks["delta"],
            gamma              = greeks["gamma"],
            theta              = greeks["theta"],
            vega               = greeks["vega"],
            iv                 = greeks["iv"],
            open_interest      = greeks.get("oi", 0),
            pop                = pop,
            contracts          = sizing["contracts"],
            risk_dollars       = sizing["risk_dollars"],
            risk_pct           = sizing["risk_pct"],
            stop_level         = exits.get("stop_debit", exits.get("stop_value", 0)),
            profit_target      = exits["profit_target"],
            stop_note          = exits["stop_note"],
            target_note        = exits["target_note"],
            rationale          = rationale,
            rvol               = rvol,
            warnings           = result.warnings,
        )

        message = format_signal(payload)
        await send_signal(message)

        # 13. Log to journal
        await log_signal({
            "symbol":       symbol,
            "strategy":     decision.strategy,
            "direction":    decision.direction,
            "structure":    decision.structure,
            "sell_strike":  sell_strike,
            "buy_strike":   buy_strike,
            "spread_width": spread_width,
            "option_type":  option_type,
            "expiry":       expiry,
            "dte":          dte,
            "credit_debit": net_credit,
            "max_loss":     max_loss,
            "ivr":          ivr,
            "vix":          vix,
            "vix_regime":   regime,
            "pop":          pop,
            "delta":        greeks["delta"],
            "theta":        greeks["theta"],
            "vega":         greeks["vega"],
            "iv":           greeks["iv"],
            "contracts":    sizing["contracts"],
            "risk_dollars": sizing["risk_dollars"],
            "vwap":         vwap,
            "rvol":         rvol,
            "rationale":    rationale,
            "timestamp_et": now_et,
        })

        logger.info(
            f"{symbol}: signal sent — "
            f"{sell_strike}/{buy_strike} "
            f"width=${spread_width:.1f} "
            f"credit=${net_credit:.2f} "
            f"ratio={credit_ratio:.0%} ✓"
        )

    except Exception as e:
        logger.error(f"{symbol}: scan error — {e}", exc_info=True)


async def run_scan() -> None:
    """Main scan — fires at 09:30 ET each weekday."""
    logger.info("=" * 50)
    logger.info("Scan started")

    vix    = await get_vix()
    regime = classify_vix(vix)
    logger.info(f"VIX {vix:.1f} — regime: {regime}")

    if regime == "pause":
        await send_warning(format_vix_warning(vix, regime))
        logger.info("VIX pause — standing down")
        return

    warning = format_vix_warning(vix, regime)
    if warning:
        await send_warning(warning)

    symbols = INDEX_ONLY if regime == "spike" else ALL_SYMBOLS
    logger.info(f"Scanning {len(symbols)} symbols")

    await asyncio.gather(*[scan_ticker(s, vix, regime) for s in symbols])

    logger.info("Scan complete")
    logger.info("=" * 50)


async def schedule_loop() -> None:
    while True:
        schedule.run_pending()
        await asyncio.sleep(30)


async def main() -> None:
    await init_db()
    logger.info("Database initialised")

    for day in ("monday", "tuesday", "wednesday", "thursday", "friday"):
        getattr(schedule.every(), day).at("09:30").do(
            lambda: asyncio.create_task(run_scan())
        )

    logger.info("Scheduler armed — waiting for 09:30 ET on weekdays")

    tg_app = build_application()

    async with tg_app:
        await tg_app.start()
        await tg_app.updater.start_polling(
            drop_pending_updates = True,
            allowed_updates      = Update.ALL_TYPES,
        )
        logger.info("Telegram bot listening for commands")
        await schedule_loop()


if __name__ == "__main__":
    asyncio.run(main())
