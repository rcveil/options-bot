"""
signals/indicators.py
Computes all technical indicators from 1-min OHLCV bars.
Logs every indicator value and direction decision at INFO level.
"""

import logging
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def compute_vwap(df: pd.DataFrame) -> pd.Series:
    typical = (df["high"] + df["low"] + df["close"]) / 3
    return (typical * df["volume"]).cumsum() / df["volume"].cumsum()


def compute_ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def compute_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def compute_macd(
    series: pd.Series,
    fast: int = 12, slow: int = 26, signal: int = 9,
) -> pd.DataFrame:
    ema_fast    = compute_ema(series, fast)
    ema_slow    = compute_ema(series, slow)
    macd_line   = ema_fast - ema_slow
    signal_line = compute_ema(macd_line, signal)
    return pd.DataFrame({
        "macd":      macd_line,
        "signal":    signal_line,
        "histogram": macd_line - signal_line,
    })


def compute_orb(df: pd.DataFrame, minutes: int = 15) -> dict:
    orb_df = df.iloc[:minutes]
    return {
        "high": float(orb_df["high"].max()),
        "low":  float(orb_df["low"].min()),
    }


def compute_rvol(df: pd.DataFrame, avg_volume: float) -> float:
    if avg_volume <= 0:
        return 1.0
    return round(df["volume"].sum() / avg_volume, 2)


def get_direction_bias(
    symbol: str,
    price:  float,
    vwap:   float,
    ema9:   float,
    ema21:  float,
    orb:    dict,
) -> Optional[str]:
    """
    Requires 2 of 3 conditions to agree.
    Logs each condition result so you can see why direction is None.
    """
    signals = []

    # 1. VWAP
    if price > vwap * 1.001:
        signals.append("bullish")
        vwap_result = f"bullish (price {price:.2f} > VWAP {vwap:.2f})"
    elif price < vwap * 0.999:
        signals.append("bearish")
        vwap_result = f"bearish (price {price:.2f} < VWAP {vwap:.2f})"
    else:
        vwap_result = f"neutral (price {price:.2f} at VWAP {vwap:.2f})"

    # 2. EMA cross
    if ema9 > ema21:
        signals.append("bullish")
        ema_result = f"bullish (EMA9 {ema9:.2f} > EMA21 {ema21:.2f})"
    elif ema9 < ema21:
        signals.append("bearish")
        ema_result = f"bearish (EMA9 {ema9:.2f} < EMA21 {ema21:.2f})"
    else:
        ema_result = f"neutral (EMA9 {ema9:.2f} = EMA21 {ema21:.2f})"

    # 3. ORB
    if price > orb["high"]:
        signals.append("bullish")
        orb_result = f"bullish (price {price:.2f} > ORB high {orb['high']:.2f})"
    elif price < orb["low"]:
        signals.append("bearish")
        orb_result = f"bearish (price {price:.2f} < ORB low {orb['low']:.2f})"
    else:
        orb_result = f"neutral (price {price:.2f} inside ORB {orb['low']:.2f}–{orb['high']:.2f})"

    bull = signals.count("bullish")
    bear = signals.count("bearish")

    if bull >= 2:
        direction = "bullish"
    elif bear >= 2:
        direction = "bearish"
    else:
        direction = None

    logger.info(
        f"{symbol}: VWAP={vwap_result} | EMA={ema_result} | ORB={orb_result} "
        f"→ direction={direction} (bull={bull} bear={bear})"
    )

    return direction


def run_all(df: pd.DataFrame, symbol: str = "?") -> dict:
    """
    Run all indicators on a 1-min bar DataFrame.
    Returns flat dict of latest values.
    """
    if df.empty or len(df) < 5:
        logger.warning(f"{symbol}: not enough bars to compute indicators ({len(df)} bars)")
        return {}

    close  = df["close"]
    latest = float(close.iloc[-1])

    vwap_s  = compute_vwap(df)
    ema9_s  = compute_ema(close, 9)
    ema21_s = compute_ema(close, 21)
    rsi_s   = compute_rsi(close, 14)
    macd_df = compute_macd(close)

    vwap  = float(vwap_s.iloc[-1])
    ema9  = float(ema9_s.iloc[-1])
    ema21 = float(ema21_s.iloc[-1])
    rsi   = float(rsi_s.iloc[-1])   if not rsi_s.isna().all()          else 50.0
    macd_hist = float(macd_df["histogram"].iloc[-1]) \
                if not macd_df["histogram"].isna().all() else 0.0

    orb       = compute_orb(df, minutes=15)
    direction = get_direction_bias(symbol, latest, vwap, ema9, ema21, orb)

    logger.info(
        f"{symbol}: price={latest:.2f} VWAP={vwap:.2f} "
        f"EMA9={ema9:.2f} EMA21={ema21:.2f} "
        f"RSI={rsi:.1f} MACD_hist={macd_hist:.4f} "
        f"ORB={orb['low']:.2f}–{orb['high']:.2f}"
    )

    return {
        "price":     round(latest,    4),
        "vwap":      round(vwap,      4),
        "ema9":      round(ema9,      4),
        "ema21":     round(ema21,     4),
        "rsi":       round(rsi,       2),
        "macd_hist": round(macd_hist, 4),
        "orb_high":  round(orb["high"], 4),
        "orb_low":   round(orb["low"],  4),
        "direction": direction,
    }
