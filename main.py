"""
main.py
Scheduler and main scan loop.
Scans every 10 minutes during market hours: 09:30–16:00 ET weekdays.
Uses pure async time checking — no schedule library.
Heartbeat every 5 minutes confirms loop is alive.
/scan command triggers scan immediately from Telegram.
"""

import asyncio
import logging
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
from data.candles import fetch_intraday_bars, fetch_avg_volume
from signals.indicators import run_all, compute_rvol
from signals.strategy import select_strategy, compute_pop, compute_position_size
from signals.chain import build_spread, build_iron_condor, build_butterfly
from signals.filters import check_credit_spread, check_debit_spread, check_iron_condor, check_butterfly
from signals.sizing import credit_exits, debit_exits, butterfly_exits
from alerts.formatter import SignalPayload, format_signal, format_vix_warning
from alerts.telegram import send_signal, send_warning, build_application
from storage.journal import init_db, log_signal

logging.basicConfig(
    level  = logging.INFO,
    format = "%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger("main")
ET     = ZoneInfo(TIMEZONE)

# Market hours ET
MARKET_OPEN_HOUR   = 9
MARKET_OPEN_MINUTE = 30
MARKET_CLOSE_HOUR  = 16
MARKET_CLOSE_MINUTE = 0

# Scan interval in minutes
SCAN_INTERVAL_MINUTES = 10

# Tracks the last scan time — "YYYY-MM-DD HH:MM" in ET
# Prevents double-firing within the same 10-minute window
_last_scan_slot: str = ""


def _is_market_open(now: datetime) -> bool:
    """True if now is within 09:30–16:00 ET on a weekday."""
    if now.weekday() >= 5:
        return False
    market_open  = now.replace(hour=MARKET_OPEN_HOUR,  minute=MARKET_OPEN_MINUTE,  second=0, microsecond=0)
    market_close = now.replace(hour=MARKET_CLOSE_HOUR, minute=MARKET_CLOSE_MINUTE, second=0, microsecond=0)
    return market_open <= now < market_close


def _current_scan_slot(now: datetime) -> str:
    """
    Returns a string representing the current 10-minute slot.
    e.g. "2026-04-22 09:30" for any time between 09:30 and 09:39.
    """
    slot_minute = (now.minute // SCAN_INTERVAL_MINUTES) * SCAN_INTERVAL_MINUTES
    return now.strftime(f"%Y-%m-%d %H:") + f"{slot_minute:02d}"


async def _scan_iron_condor(
    symbol: str, vix: float, regime: str, ivr: float,
    price: float, vwap: float, rvol: float, ind: dict, now_et: str,
) -> None:
    ic = await build_iron_condor(
        symbol=symbol, underlying_price=price,
        dte_min=DTE_CREDIT_MIN, dte_max=DTE_CREDIT_MAX,
    )
    if ic is None:
        logger.info(f"{symbol} IC: no valid condor")
        return

    put_g        = ic["put_greeks"]
    call_g       = ic["call_greeks"]
    put_pop      = compute_pop(price, ic["put_sell_strike"], ic["dte"], put_g["iv"],  "P")
    call_pop     = compute_pop(price, ic["call_sell_strike"], ic["dte"], call_g["iv"], "C")
    combined_pop = round(put_pop * call_pop, 4)

    result = check_iron_condor(
        put_credit=ic["put_credit"],    put_width=ic["put_spread_width"],
        put_delta=put_g["delta"],       put_bid_ask=put_g["ask"] - put_g["bid"],
        put_mid=put_g["mid"],           put_oi=put_g.get("oi", 999),
        call_credit=ic["call_credit"],  call_width=ic["call_spread_width"],
        call_delta=call_g["delta"],     call_bid_ask=call_g["ask"] - call_g["bid"],
        call_mid=call_g["mid"],         call_oi=call_g.get("oi", 999),
        pop=combined_pop, dte=ic["dte"], symbol=symbol, vix_regime=regime,
    )

    if not result.passed:
        logger.info(f"{symbol} IC: REJECTED — {'; '.join(result.reasons)}")
        return

    sizing    = compute_position_size(ACCOUNT_SIZE, ic["max_loss"] * 100, regime)
    exits     = credit_exits(ic["total_credit"])
    rationale = (
        f"No directional bias. IVR {ivr:.0f} — selling premium both sides. "
        f"Price {'above' if price > vwap else 'below'} VWAP. RSI {ind['rsi']:.0f}."
    )

    payload = SignalPayload(
        symbol=symbol, direction="neutral",
        strategy="iron_condor", structure="credit",
        vix=vix, vix_regime=regime, timestamp_et=now_et,
        sell_strike=ic["put_sell_strike"], buy_strike=ic["put_buy_strike"],
        expiry=ic["expiry"], dte=ic["dte"],
        credit_debit=ic["total_credit"], max_loss=ic["max_loss"],
        put_sell_strike=ic["put_sell_strike"], put_buy_strike=ic["put_buy_strike"],
        put_credit=ic["put_credit"], put_credit_ratio=ic["put_credit_ratio"],
        call_sell_strike=ic["call_sell_strike"], call_buy_strike=ic["call_buy_strike"],
        call_credit=ic["call_credit"], call_credit_ratio=ic["call_credit_ratio"],
        wing_width=ic["wing_width"], ivr=ivr,
        credit_width_ratio=min(ic["put_credit_ratio"], ic["call_credit_ratio"]),
        bid_ask_spread=put_g["ask"] - put_g["bid"], mid_price=put_g["mid"],
        delta=put_g["delta"], gamma=put_g.get("gamma", 0.0),
        theta=put_g.get("theta", 0.0), vega=put_g.get("vega", 0.0),
        iv=put_g["iv"], open_interest=put_g.get("oi", 0),
        pop=combined_pop, contracts=sizing["contracts"],
        risk_dollars=sizing["risk_dollars"], risk_pct=sizing["risk_pct"],
        stop_level=exits["stop_debit"], profit_target=exits["profit_target"],
        stop_note=exits["stop_note"], target_note=exits["target_note"],
        rationale=rationale, rvol=rvol, warnings=result.warnings,
    )

    await send_signal(format_signal(payload))
    await log_signal({
        "symbol": symbol, "strategy": "iron_condor",
        "direction": "neutral", "structure": "credit",
        "sell_strike": None, "buy_strike": None,
        "spread_width": None, "option_type": None,
        "put_sell_strike": ic["put_sell_strike"], "put_buy_strike": ic["put_buy_strike"],
        "put_credit": ic["put_credit"], "put_credit_ratio": ic["put_credit_ratio"],
        "call_sell_strike": ic["call_sell_strike"], "call_buy_strike": ic["call_buy_strike"],
        "call_credit": ic["call_credit"], "call_credit_ratio": ic["call_credit_ratio"],
        "wing_width": ic["wing_width"],
        "expiry": ic["expiry"], "dte": ic["dte"],
        "credit_debit": ic["total_credit"], "max_loss": ic["max_loss"],
        "ivr": ivr, "vix": vix, "vix_regime": regime, "pop": combined_pop,
        "delta": put_g["delta"], "theta": put_g.get("theta", 0.0),
        "vega": put_g.get("vega", 0.0), "iv": put_g["iv"],
        "contracts": sizing["contracts"], "risk_dollars": sizing["risk_dollars"],
        "vwap": vwap, "rvol": rvol, "rationale": rationale, "timestamp_et": now_et,
    })
    logger.info(f"{symbol} IC: SIGNAL SENT ✓")


async def _scan_vertical(
    symbol: str, decision, vix: float, regime: str, ivr: float,
    price: float, vwap: float, rvol: float, ind: dict, now_et: str,
) -> None:
    dte_min, dte_max = decision.dte_target
    spread = await build_spread(
        symbol=symbol, structure=decision.structure,
        direction=decision.direction, underlying_price=price,
        dte_min=dte_min, dte_max=dte_max,
    )
    if spread is None:
        logger.info(f"{symbol}: chain returned None")
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
    pop          = compute_pop(price, sell_strike, dte, greeks["iv"], option_type)

    logger.info(
        f"{symbol}: {sell_strike}/{buy_strike} width=${spread_width:.1f} "
        f"credit=${net_credit:.2f} ratio={credit_ratio:.0%} pop={pop:.0%}"
    )

    if decision.structure == "credit":
        result = check_credit_spread(
            credit=net_credit, width=spread_width, pop=pop,
            bid_ask_spread=ba_spread, mid_price=greeks["mid"],
            short_delta=greeks["delta"], dte=dte,
            open_interest=greeks.get("oi", 999),
            symbol=symbol, vix_regime=regime,
        )
    else:
        result = check_debit_spread(
            debit=net_credit, width=spread_width, pop=pop,
            bid_ask_spread=ba_spread, mid_price=greeks["mid"],
            long_delta=greeks["delta"], dte=dte,
            open_interest=greeks.get("oi", 999),
            symbol=symbol, vix_regime=regime,
        )

    if not result.passed:
        logger.info(f"{symbol}: REJECTED — {'; '.join(result.reasons)}")
        return

    sizing    = compute_position_size(ACCOUNT_SIZE, max_loss * 100, regime)
    exits     = credit_exits(net_credit) if decision.structure == "credit" \
                else debit_exits(net_credit)
    rationale = (
        f"{decision.rationale} "
        f"Price {'above' if price > vwap else 'below'} VWAP ({price:.2f} vs {vwap:.2f}). "
        f"RSI {ind['rsi']:.0f}. "
        f"MACD {'positive' if ind['macd_hist'] > 0 else 'negative'}. "
        f"ORB {'broken' if (price > ind['orb_high'] or price < ind['orb_low']) else 'intact'}."
    )

    payload = SignalPayload(
        symbol=symbol, direction=decision.direction,
        strategy=decision.strategy, structure=decision.structure,
        vix=vix, vix_regime=regime, timestamp_et=now_et,
        sell_strike=sell_strike, buy_strike=buy_strike,
        expiry=expiry, dte=dte, credit_debit=net_credit, max_loss=max_loss,
        ivr=ivr, credit_width_ratio=credit_ratio,
        bid_ask_spread=ba_spread, mid_price=greeks["mid"],
        delta=greeks["delta"], gamma=greeks.get("gamma", 0.0),
        theta=greeks.get("theta", 0.0), vega=greeks.get("vega", 0.0),
        iv=greeks["iv"], open_interest=greeks.get("oi", 0),
        pop=pop, contracts=sizing["contracts"],
        risk_dollars=sizing["risk_dollars"], risk_pct=sizing["risk_pct"],
        stop_level=exits.get("stop_debit", exits.get("stop_value", 0)),
        profit_target=exits["profit_target"],
        stop_note=exits["stop_note"], target_note=exits["target_note"],
        rationale=rationale, rvol=rvol, warnings=result.warnings,
    )

    await send_signal(format_signal(payload))
    await log_signal({
        "symbol": symbol, "strategy": decision.strategy,
        "direction": decision.direction, "structure": decision.structure,
        "sell_strike": sell_strike, "buy_strike": buy_strike,
        "spread_width": spread_width, "option_type": option_type,
        "expiry": expiry, "dte": dte,
        "credit_debit": net_credit, "max_loss": max_loss,
        "ivr": ivr, "vix": vix, "vix_regime": regime, "pop": pop,
        "delta": greeks["delta"], "theta": greeks.get("theta", 0.0),
        "vega": greeks.get("vega", 0.0), "iv": greeks["iv"],
        "contracts": sizing["contracts"], "risk_dollars": sizing["risk_dollars"],
        "vwap": vwap, "rvol": rvol, "rationale": rationale, "timestamp_et": now_et,
    })
    logger.info(f"{symbol}: SIGNAL SENT ✓ {sell_strike}/{buy_strike} credit=${net_credit:.2f}")


async def _scan_butterfly(
    symbol: str, vix: float, regime: str, ivr: float,
    price: float, vwap: float, rvol: float, ind: dict, now_et: str,
) -> None:
    dte_min, dte_max = 25, 35
    bf = await build_butterfly(
        symbol=symbol, underlying_price=price,
        dte_min=dte_min, dte_max=dte_max,
    )
    if bf is None:
        logger.info(f"{symbol} BF: no valid butterfly")
        return

    body_g = bf["body_greeks"]
    result = check_butterfly(
        debit        = bf["net_debit"],
        wing_width   = bf["wing_width"],
        max_profit   = bf["max_profit"],
        net_delta    = bf["net_delta"],
        body_bid_ask = body_g["ask"] - body_g["bid"],
        body_mid     = body_g["mid"],
        dte          = bf["dte"],
        open_interest= body_g.get("oi", 999),
        symbol       = symbol,
        vix_regime   = regime,
    )

    if not result.passed:
        logger.info(f"{symbol} BF: REJECTED — {'; '.join(result.reasons)}")
        return

    sizing = compute_position_size(ACCOUNT_SIZE, bf["max_loss"] * 100, regime)
    exits  = butterfly_exits(bf["net_debit"], bf["max_profit"])
    profit_multiple = round(bf["max_profit"] / bf["net_debit"], 1) if bf["net_debit"] > 0 else 0
    rationale = (
        f"No directional bias. IVR {ivr:.0f} in 30–50 range — "
        f"butterfly lottery ticket. High IV reduces net debit. "
        f"Price at ${price:.2f}, body strike ${bf['body_strike']:.0f}. "
        f"RSI {ind['rsi']:.0f}. Max profit {profit_multiple}x debit if stock pins body at expiry."
    )

    payload = SignalPayload(
        symbol=symbol, direction="neutral",
        strategy="long_butterfly", structure="debit",
        vix=vix, vix_regime=regime, timestamp_et=now_et,
        expiry=bf["expiry"], dte=bf["dte"],
        credit_debit=bf["net_debit"], max_loss=bf["max_loss"],
        ivr=ivr, pop=0.0,   # PoP is not standard for butterfly — depends on pinning
        contracts=sizing["contracts"],
        risk_dollars=sizing["risk_dollars"], risk_pct=sizing["risk_pct"],
        stop_level=exits["stop_value"], profit_target=exits["profit_target"],
        stop_note=exits["stop_note"], target_note=exits["target_note"],
        rationale=rationale, rvol=rvol, warnings=result.warnings,
        # Butterfly-specific fields
        lower_strike=bf["lower_strike"],
        body_strike=bf["body_strike"],
        upper_strike=bf["upper_strike"],
        wing_width=bf["wing_width"],
        debit_ratio=bf["debit_ratio"],
        max_profit=bf["max_profit"],
        net_delta=bf["net_delta"],
        # Greeks from body (short legs)
        delta=body_g["delta"], gamma=body_g.get("gamma", 0.0),
        theta=body_g.get("theta", 0.0), vega=body_g.get("vega", 0.0),
        iv=body_g["iv"], open_interest=body_g.get("oi", 0),
        bid_ask_spread=body_g["ask"] - body_g["bid"],
        mid_price=body_g["mid"],
    )

    await send_signal(format_signal(payload))
    await log_signal({
        "symbol": symbol, "strategy": "long_butterfly",
        "direction": "neutral", "structure": "debit",
        "sell_strike": bf["body_strike"], "buy_strike": bf["lower_strike"],
        "spread_width": bf["wing_width"], "option_type": "C",
        "expiry": bf["expiry"], "dte": bf["dte"],
        "credit_debit": bf["net_debit"], "max_loss": bf["max_loss"],
        "ivr": ivr, "vix": vix, "vix_regime": regime, "pop": 0.0,
        "delta": body_g["delta"], "theta": body_g.get("theta", 0.0),
        "vega": body_g.get("vega", 0.0), "iv": body_g["iv"],
        "contracts": sizing["contracts"], "risk_dollars": sizing["risk_dollars"],
        "vwap": vwap, "rvol": rvol, "rationale": rationale, "timestamp_et": now_et,
    })
    logger.info(f"{symbol} BF: SIGNAL SENT ✓ {bf['lower_strike']}/{bf['body_strike']}/{bf['upper_strike']} debit=${bf['net_debit']:.2f}")


async def scan_ticker(symbol: str, vix: float, regime: str) -> None:
    try:
        df = await fetch_intraday_bars(symbol)
        if df.empty or len(df) < 5:
            logger.info(f"{symbol}: SKIP — only {len(df)} bars")
            return

        avg_vol  = await fetch_avg_volume(symbol)
        rvol     = compute_rvol(df, avg_vol)
        ind      = run_all(df, symbol=symbol)
        if not ind:
            logger.info(f"{symbol}: SKIP — indicators empty")
            return

        direction = ind["direction"]
        price     = ind["price"]
        vwap      = ind["vwap"]
        ivr       = await get_ivr(symbol)
        now_et    = datetime.now(ET).strftime("%H:%M ET")
        decision  = select_strategy(direction, ivr, regime)

        logger.info(
            f"{symbol}: direction={direction} IVR={ivr:.0f} "
            f"strategy={decision.strategy}"
        )

        if decision.strategy == "no_trade":
            logger.info(f"{symbol}: SKIP no_trade")
            return

        if decision.strategy == "iron_condor":
            await _scan_iron_condor(
                symbol, vix, regime, ivr, price, vwap, rvol, ind, now_et
            )
        elif decision.strategy == "long_butterfly":
            await _scan_butterfly(
                symbol, vix, regime, ivr, price, vwap, rvol, ind, now_et
            )
        else:
            await _scan_vertical(
                symbol, decision, vix, regime, ivr,
                price, vwap, rvol, ind, now_et
            )

    except Exception as e:
        logger.error(f"{symbol}: ERROR — {e}", exc_info=True)


async def run_scan() -> None:
    """
    Run full scan across all symbols.
    Called by scheduler every 10 minutes during market hours,
    and by /scan command for manual triggers.
    """
    global _last_scan_slot

    now  = datetime.now(ET)
    slot = _current_scan_slot(now)

    # Prevent double-firing within the same 10-minute window
    if _last_scan_slot == slot:
        logger.info(f"Scan already ran for slot {slot} — skipping")
        return
    _last_scan_slot = slot

    logger.info("=" * 60)
    logger.info(f"SCAN STARTED {now.strftime('%A %Y-%m-%d %H:%M %Z')}")

    try:
        vix    = await get_vix()
        regime = classify_vix(vix)
    except Exception as e:
        logger.error(f"VIX fetch failed: {e} — using 20.0")
        vix, regime = 20.0, "elevated"

    logger.info(f"VIX={vix:.1f} regime={regime}")

    if regime == "pause":
        await send_warning(format_vix_warning(vix, regime))
        logger.info("VIX pause — standing down")
        logger.info("=" * 60)
        return

    warning = format_vix_warning(vix, regime)
    if warning:
        await send_warning(warning)

    symbols = INDEX_ONLY if regime == "spike" else ALL_SYMBOLS
    logger.info(f"Scanning {len(symbols)} symbols")

    for s in symbols:
        await scan_ticker(s, vix, regime)

    logger.info("SCAN COMPLETE")
    logger.info("=" * 60)


async def scheduler_loop() -> None:
    """
    Checks every 30 seconds:
    - Is it a weekday between 09:30 and 16:00 ET?
    - Has 10 minutes elapsed since the last scan?
    If both true, fires run_scan().
    Heartbeat log every 5 minutes.
    """
    logger.info(
        f"Scheduler started — scanning every {SCAN_INTERVAL_MINUTES} min "
        f"during market hours (09:30–16:00 ET weekdays)"
    )
    heartbeat_counter = 0

    while True:
        now = datetime.now(ET)

        heartbeat_counter += 1
        if heartbeat_counter >= 10:   # every 10 × 30s = 5 minutes
            logger.info(
                f"Scheduler heartbeat — "
                f"{now.strftime('%A %H:%M %Z')} "
                f"market_open={_is_market_open(now)}"
            )
            heartbeat_counter = 0

        if _is_market_open(now):
            # Fire at the start of each 10-minute slot
            slot_second = (now.minute % SCAN_INTERVAL_MINUTES) * 60 + now.second
            if slot_second < 30:    # within the first 30s of a new slot
                slot = _current_scan_slot(now)
                if slot != _last_scan_slot:
                    logger.info(
                        f"SCAN TRIGGER: {now.strftime('%A %H:%M %Z')} "
                        f"(slot={slot})"
                    )
                    await run_scan()

        await asyncio.sleep(30)


async def main() -> None:
    await init_db()

    now = datetime.now(ET)
    logger.info(f"Bot starting — {now.strftime('%A %Y-%m-%d %H:%M %Z')}")
    logger.info(
        f"Scan schedule: every {SCAN_INTERVAL_MINUTES} min "
        f"from 09:30 to 16:00 ET weekdays "
        f"(up to {(390 // SCAN_INTERVAL_MINUTES)} scans per day)"
    )

    tg_app = build_application()
    async with tg_app:
        await tg_app.start()
        await tg_app.updater.start_polling(
            drop_pending_updates=True,
            allowed_updates=Update.ALL_TYPES,
        )
        logger.info("Telegram bot listening — use /scan to trigger manually")
        await scheduler_loop()


if __name__ == "__main__":
    asyncio.run(main())
