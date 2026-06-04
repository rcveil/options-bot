"""
backtest/engine.py
P&L calculation at expiry for each strategy type.

Convention matches handle_close in alerts/telegram.py:
  Credit: pnl = (entry_credit - spread_value) * 100 * contracts
  Debit:  pnl = (spread_value - entry_debit)  * 100 * contracts

Where spread_value = intrinsic value of the spread at expiry,
computed from the underlying's closing price on the expiry date.

sell_strike / buy_strike naming quirk (from chain.py):
  Credit spreads: sell_strike = short leg, buy_strike = long (protective) leg
  Debit spreads:  sell_strike = primary long leg (higher delta), buy_strike = short cap leg

fetch_close_price() returns the underlying closing price on the expiry date
using yfinance. If expiry falls on a non-trading day (rare) it returns the
most recent close on or before that date.
"""

import logging
from datetime import datetime, timedelta

import yfinance as yf

logger = logging.getLogger(__name__)


# ── Price fetch ────────────────────────────────────────────────────────────────

def fetch_close_price(symbol: str, expiry_date: str) -> float | None:
    """
    Fetch the underlying closing price on expiry_date via yfinance.
    Looks at a 5-day window ending on expiry_date and returns the
    last available close on or before that date.
    Returns None on failure.
    """
    try:
        exp = datetime.strptime(expiry_date, "%Y-%m-%d")
        # Window: 5 calendar days before expiry to 1 day after
        # Handles weekends and holidays by ensuring we find the prior close
        start = (exp - timedelta(days=5)).strftime("%Y-%m-%d")
        end   = (exp + timedelta(days=1)).strftime("%Y-%m-%d")

        df = yf.download(symbol, start=start, end=end, progress=False, auto_adjust=True)
        if df.empty:
            logger.warning(f"yfinance: no data for {symbol} around {expiry_date}")
            return None

        # Flatten MultiIndex columns if present (newer yfinance versions)
        if hasattr(df.columns, "levels"):
            df.columns = df.columns.get_level_values(0)

        # Take the last row on or before the expiry date
        close_series = df["Close"]
        target = exp.date()
        available = [d.date() for d in close_series.index]
        on_or_before = [d for d in available if d <= target]
        if not on_or_before:
            logger.warning(f"yfinance: no trading day on or before {expiry_date} for {symbol}")
            return None

        latest = max(on_or_before)
        idx    = [d.date() for d in close_series.index].index(latest)
        close  = float(close_series.iloc[idx])
        logger.info(f"{symbol} close on {latest} (expiry {expiry_date}): {close:.2f}")
        return round(close, 4)

    except Exception as e:
        logger.warning(f"fetch_close_price failed for {symbol} {expiry_date}: {e}")
        return None


# ── Payoff helpers ─────────────────────────────────────────────────────────────

def _intrinsic_call(strike: float, underlying: float) -> float:
    return max(0.0, underlying - strike)


def _intrinsic_put(strike: float, underlying: float) -> float:
    return max(0.0, strike - underlying)


# ── Per-strategy P&L ──────────────────────────────────────────────────────────

def _pnl_vertical(signal: dict, ul: float) -> float:
    """
    P&L for bull_put_spread, bear_call_spread, bull_call_spread, bear_put_spread.

    sell_strike / buy_strike semantics:
      bull_put_spread  (credit, P): sell put @ sell_strike (higher), buy put @ buy_strike (lower)
      bear_call_spread (credit, C): sell call @ sell_strike (lower), buy call @ buy_strike (higher)
      bull_call_spread (debit, C):  long call @ sell_strike (lower delta~0.50), short call @ buy_strike (higher)
      bear_put_spread  (debit, P):  long put @ sell_strike (higher delta~0.50), short put @ buy_strike (lower)
    """
    sell    = float(signal["sell_strike"])
    buy     = float(signal["buy_strike"])
    credit  = float(signal["credit_debit"])
    contracts = int(signal.get("contracts") or 1)
    strategy  = signal["strategy"]
    structure = signal["structure"]

    if strategy == "bull_put_spread":
        # Short put @ sell, long put @ buy (sell > buy)
        spread_value = _intrinsic_put(sell, ul) - _intrinsic_put(buy, ul)
        return round((credit - spread_value) * 100 * contracts, 2)

    if strategy == "bear_call_spread":
        # Short call @ sell, long call @ buy (buy > sell)
        spread_value = _intrinsic_call(sell, ul) - _intrinsic_call(buy, ul)
        return round((credit - spread_value) * 100 * contracts, 2)

    if strategy == "bull_call_spread":
        # Long call @ sell_strike (lower), short call @ buy_strike (higher)
        spread_value = _intrinsic_call(sell, ul) - _intrinsic_call(buy, ul)
        return round((spread_value - credit) * 100 * contracts, 2)

    if strategy == "bear_put_spread":
        # Long put @ sell_strike (higher), short put @ buy_strike (lower)
        spread_value = _intrinsic_put(sell, ul) - _intrinsic_put(buy, ul)
        return round((spread_value - credit) * 100 * contracts, 2)

    raise ValueError(f"_pnl_vertical called with unexpected strategy: {strategy}")


def _pnl_iron_condor(signal: dict, ul: float) -> float:
    """
    P&L for iron_condor.
    Put wing:  short put @ put_sell_strike, long put @ put_buy_strike
    Call wing: short call @ call_sell_strike, long call @ call_buy_strike
    """
    put_sell  = float(signal["put_sell_strike"])
    put_buy   = float(signal["put_buy_strike"])
    call_sell = float(signal["call_sell_strike"])
    call_buy  = float(signal["call_buy_strike"])
    total_credit = float(signal["credit_debit"])
    contracts    = int(signal.get("contracts") or 1)

    put_wing_value  = _intrinsic_put(put_sell,   ul) - _intrinsic_put(put_buy,   ul)
    call_wing_value = _intrinsic_call(call_sell, ul) - _intrinsic_call(call_buy, ul)
    spread_value    = put_wing_value + call_wing_value

    return round((total_credit - spread_value) * 100 * contracts, 2)


def _pnl_jade_lizard(signal: dict, ul: float) -> float:
    """
    P&L for jade_lizard.
    Leg 1: naked short put @ jl_put_strike
    Leg 2: short call @ jl_short_call_strike
    Leg 3: long call  @ jl_long_call_strike
    The short put has theoretically unbounded downside below breakeven.
    """
    put_strike  = float(signal["jl_put_strike"])
    short_call  = float(signal["jl_short_call_strike"])
    long_call   = float(signal["jl_long_call_strike"])
    total_credit = float(signal["credit_debit"])
    contracts    = int(signal.get("contracts") or 1)

    put_value        = _intrinsic_put(put_strike, ul)
    call_spread_value = _intrinsic_call(short_call, ul) - _intrinsic_call(long_call, ul)
    spread_value     = put_value + call_spread_value

    return round((total_credit - spread_value) * 100 * contracts, 2)


# ── Main dispatcher ────────────────────────────────────────────────────────────

def calc_pnl(signal: dict, underlying_close: float) -> float | None:
    """
    Calculate P&L at expiry for a signal given the underlying's closing price.
    Returns P&L in dollars (positive = profit, negative = loss).
    Returns None for unsupported strategies (long_butterfly in v1).

    The exit_price parameter is provided for forward compatibility with
    Approach 2 (intra-trade exit simulation) — pass underlying_close for now.
    """
    strategy = signal.get("strategy", "")

    try:
        if strategy in ("bull_put_spread", "bear_call_spread",
                        "bull_call_spread", "bear_put_spread"):
            return _pnl_vertical(signal, underlying_close)

        if strategy == "iron_condor":
            return _pnl_iron_condor(signal, underlying_close)

        if strategy == "jade_lizard":
            return _pnl_jade_lizard(signal, underlying_close)

        if strategy == "long_butterfly":
            logger.info(f"calc_pnl: long_butterfly not supported in v1, skipping signal id={signal.get('id')}")
            return None

        logger.warning(f"calc_pnl: unknown strategy '{strategy}', skipping signal id={signal.get('id')}")
        return None

    except (TypeError, KeyError, ValueError) as e:
        logger.warning(f"calc_pnl failed for signal id={signal.get('id')} strategy={strategy}: {e}")
        return None
