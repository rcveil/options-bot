"""
main.py
Scheduler and main scan loop.
Handles vertical spreads and iron condors.
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
from signals.chain import build_spread, build_iron_condor
from signals.filters import (
    check_credit_spread, check_debit_spread, check_iron_condor
)
from signals.sizing import credit_exits, debit_exits
from alerts.formatter import SignalPayload, format_signal, format_vix_warning
from alerts.telegram import send_signal, send_warning, build_application
from storage.journal import init_db, log_signal

logging.basicConfig(
    level  = logging.INFO,
    format = "%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger("main")
ET     = ZoneInfo(TIMEZONE)


async def _scan_iron_condor(
    symbol: str,
    vix:    float,
    regime: str,
    ivr:    float,
    price:  float,
    vwap:   float,
    rvol:   float,
    ind:    dict,
    now_et: str,
) -> None:
    """Handle iron condor strategy for one symbol."""

    ic = await build_iron_condor(
        symbol           = symbol,
        underlying_price = price,
        dte_min          = DTE_CREDIT_MIN,
        dte_max          = DTE_CREDIT_MAX,
    )
    if ic is None:
        logger.info(f"{symbol} IC: no valid condor found")
        return

    put_g  = ic["put_greeks"]
    call_g = ic["call_greeks"]

    # Combined PoP = P(put OTM) × P(call OTM)
    put_pop  = compute_pop(price, ic["put_sell_strike"],
                           ic["dte"], put_g["iv"],  "P")
    call_pop = compute_pop(price, ic["call_sell_strike"],
                           ic["dte"], call_g["iv"], "C")
    combined_pop = round(put_pop * call_pop, 4)

    result = check_iron_condor(
        put_credit    = ic["put_credit"],
        put_width     = ic["put_spread_width"],
        put_delta     = put_g["delta"],
        put_bid_ask   = put_g["ask"] - put_g["bid"],
        put_mid       = put_g["mid"],
        put_oi        = put_g.get("oi", 999),
        call_credit   = ic["call_credit"],
        call_width    = ic["call_spread_width"],
        call_delta    = call_g["delta"],
        call_bid_ask  = call_g["ask"] - call_g["bid"],
        call_mid      = call_g["mid"],
        call_oi       = call_g.get("oi", 999),
        pop           = combined_pop,
        dte           = ic["dte"],
        symbol        = symbol,
        vix_regime    = regime,
    )

    if not result.passed:
        logger.info(f"{symbol} IC: filtered — {'; '.join(result.reasons)}")
        return

    sizing = compute_position_size(
        account_size = ACCOUNT_SIZE,
        max_loss     = ic["max_loss"] * 100,
        vix_regime   = regime,
    )

    exits = credit_exits(ic["total_credit"])

    rationale = (
        f"No directional bias. IVR {ivr:.0f} elevated — selling premium both sides. "
        f"Price {'above' if price > vwap else 'below'} VWAP ({price:.2f} vs {vwap:.2f}). "
        f"RSI {ind['rsi']:.0f}. RVOL {rvol:.1f}x."
    )

    payload = SignalPayload(
        symbol             = symbol,
        direction          = "neutral",
        strategy           = "iron_condor",
        structure          = "credit",
        vix                = vix,
        vix_regime         = regime,
        timestamp_et       = now_et,
        # Vertical fields — use put wing as primary display
        sell_strike        = ic["put_sell_strike"],
        buy_strike         = ic["put_buy_strike"],
        expiry             = ic["expiry"],
        dte                = ic["dte"],
        credit_debit       = ic["total_credit"],
        max_loss           = ic["max_loss"],
        # IC-specific fields
        put_sell_strike    = ic["put_sell_strike"],
        put_buy_strike     = ic["put_buy_strike"],
        put_credit         = ic["put_credit"],
        put_credit_ratio   = ic["put_credit_ratio"],
        call_sell_strike   = ic["call_sell_strike"],
        call_buy_strike    = ic["call_buy_strike"],
        call_credit        = ic["call_credit"],
        call_credit_ratio  = ic["call_credit_ratio"],
        wing_width         = ic["wing_width"],
        # Thresholds
        ivr                = ivr,
        credit_width_ratio = min(ic["put_credit_ratio"], ic["call_credit_ratio"]),
        bid_ask_spread     = put_g["ask"] - put_g["bid"],
        mid_price          = put_g["mid"],
        # Greeks (put short leg)
        delta              = put_g["delta"],
        gamma              = put_g.get("gamma", 0.0),
        theta              = put_g.get("theta", 0.0),
        vega               = put_g.get("vega",  0.0),
        iv                 = put_g["iv"],
        open_interest      = put_g.get("oi", 0),
        # Sizing
        pop                = combined_pop,
        contracts          = sizing["contracts"],
        risk_dollars       = sizing["risk_dollars"],
        risk_pct           = sizing["risk_pct"],
        stop_level         = exits["stop_debit"],
        profit_target      = exits["profit_target"],
        stop_note          = exits["stop_note"],
        target_note        = exits["target_note"],
        rationale          = rationale,
        rvol               = rvol,
        warnings           = result.warnings,
    )

    await send_signal(format_signal(payload))

    await log_signal({
        "symbol":           symbol,
        "strategy":         "iron_condor",
        "direction":        "neutral",
        "structure":        "credit",
        "sell_strike":      None,
        "buy_strike":       None,
        "spread_width":     None,
        "option_type":      None,
        "put_sell_strike":  ic["put_sell_strike"],
        "put_buy_strike":   ic["put_buy_strike"],
        "put_credit":       ic["put_credit"],
        "put_credit_ratio": ic["put_credit_ratio"],
        "call_sell_strike": ic["call_sell_strike"],
        "call_buy_strike":  ic["call_buy_strike"],
        "call_credit":      ic["call_credit"],
        "call_credit_ratio":ic["call_credit_ratio"],
        "wing_width":       ic["wing_width"],
        "expiry":           ic["expiry"],
        "dte":              ic["dte"],
        "credit_debit":     ic["total_credit"],
        "max_loss":         ic["max_loss"],
        "ivr":              ivr,
        "vix":              vix,
        "vix_regime":       regime,
        "pop":              combined_pop,
        "delta":            put_g["delta"],
        "theta":            put_g.get("theta", 0.0),
        "vega":             put_g.get("vega",  0.0),
        "iv":               put_g["iv"],
        "contracts":        sizing["contracts"],
        "risk_dollars":     sizing["risk_dollars"],
        "vwap":             vwap,
        "rvol":             rvol,
        "rationale":        rationale,
        "timestamp_et":     now_et,
    })

    logger.info(f"{symbol} IC: signal sent ✓")


async def _scan_vertical(
    symbol:   str,
    decision,
    vix:      float,
    regime:   str,
    ivr:      float,
    price:    float,
    vwap:     float,
    rvol:     float,
    ind:      dict,
    now_et:   str,
) -> None:
    """Handle vertical spread strategy for one symbol."""

    dte_min, dte_max = decision.dte_target

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
    greeks       = spread["greeks"]
    option_type  = "P" if "put" in decision.strategy else "C"
    net_credit   = spread["net_credit"]
    credit_ratio = spread["credit_ratio"]
    max_loss     = spread["max_loss"]
    ba_spread    = greeks["ask"] - greeks["bid"]

    pop = compute_pop(price, sell_strike, dte, greeks["iv"], option_type)

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
        logger.info(f"{symbol}: filtered — {'; '.join(result.reasons)}")
        return

    sizing = compute_position_size(
        account_size = ACCOUNT_SIZE,
        max_loss     = max_loss * 100,
        vix_regime   = regime,
    )

    exits = credit_exits(net_credit) if decision.structure == "credit" \
            else debit_exits(net_credit)

    rationale = (
        f"{decision.rationale} "
        f"Price {'above' if price > vwap else 'below'} VWAP "
        f"({price:.2f} vs {vwap:.2f}). "
        f"RSI {ind['rsi']:.0f}. "
        f"MACD {'positive' if ind['macd_hist'] > 0 else 'negative'}. "
        f"ORB {'broken' if (price > ind['orb_high'] or price < ind['orb_low']) else 'intact'}."
    )

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
        gamma              = greeks.get("gamma", 0.0),
        theta              = greeks.get("theta", 0.0),
        vega               = greeks.get("vega",  0.0),
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

    await send_signal(format_signal(payload))

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
        "theta":        greeks.get("theta", 0.0),
        "vega":         greeks.get("vega",  0.0),
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
        f"credit=${net_credit:.2f} ratio={credit_ratio:.0%} ✓"
    )


async def scan_ticker(symbol: str, vix: float, regime: str) -> None:
    """Full signal pipeline for one ticker."""
    try:
        df = await fetch_intraday_bars(symbol)
        if df.empty or len(df) < 5:
            logger.info(f"{symbol}: insufficient bar data")
            return

        avg_vol = await fetch_avg_volume(symbol)
        rvol    = compute_rvol(df, avg_vol)
        ind     = run_all(df)
        if not ind:
            return

        direction = ind["direction"]
        price     = ind["price"]
        vwap      = ind["vwap"]
        ivr       = await get_ivr(symbol)
        now_et    = datetime.now(ET).strftime("%H:%M ET")

        logger.info(
            f"{symbol}: price={price:.2f} vwap={vwap:.2f} "
            f"direction={direction} ivr={ivr:.0f} rvol={rvol}"
        )

        decision = select_strategy(direction, ivr, regime)

        if decision.strategy == "no_trade":
            logger.info(f"{symbol}: no_trade")
            return

        if decision.strategy == "iron_condor":
            await _scan_iron_condor(
                symbol, vix, regime, ivr, price, vwap, rvol, ind, now_et
            )
        else:
            await _scan_vertical(
                symbol, decision, vix, regime, ivr,
                price, vwap, rvol, ind, now_et
            )

    except Exception as e:
        logger.error(f"{symbol}: scan error — {e}", exc_info=True)


async def run_scan() -> None:
    logger.info("=" * 50)
    logger.info("Scan started")

    vix    = await get_vix()
    regime = classify_vix(vix)
    logger.info(f"VIX {vix:.1f} — regime: {regime}")

    if regime == "pause":
        await send_warning(format_vix_warning(vix, regime))
        return

    warning = format_vix_warning(vix, regime)
    if warning:
        await send_warning(warning)

    symbols = INDEX_ONLY if regime == "spike" else ALL_SYMBOLS
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
