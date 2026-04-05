"""
signals/indicators.py
Computes all technical indicators from 1-min OHLCV bars.
"""

from typing import Optional
import numpy as np
import pandas as pd


def compute_vwap(df: pd.DataFrame) -> pd.Series:
    """
    Volume-Weighted Average Price.
    df must have: high, low, close, volume columns.
    """
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
    fast:   int = 12,
    slow:   int = 26,
    signal: int = 9,
) -> pd.DataFrame:
    ema_fast    = compute_ema(series, fast)
    ema_slow    = compute_ema(series, slow)
    macd_line   = ema_fast - ema_slow
    signal_line = compute_ema(macd_line, signal)
    histogram   = macd_line - signal_line
    return pd.DataFrame({
        "macd":      macd_line,
        "signal":    signal_line,
        "histogram": histogram,
    })


def compute_orb(df: pd.DataFrame, minutes: int = 15) -> dict:
    """
    Opening Range Breakout: high/low of first N 1-min bars.
    df must be indexed by datetime, sorted ascending from 09:30 ET.
    """
    orb_df = df.iloc[:minutes]
    return {
        "high": float(orb_df["high"].max()),
        "low":  float(orb_df["low"].min()),
    }


def compute_rvol(df: pd.DataFrame, avg_volume: float) -> float:
    """Relative volume: today's cumulative vol vs historical average."""
    if avg_volume <= 0:
        return 1.0
    return round(df["volume"].sum() / avg_volume, 2)


def get_direction_bias(
    price:  float,
    vwap:   float,
    ema9:   float,
    ema21:  float,
    orb:    dict,
) -> Optional[str]:
    """
    Returns 'bullish', 'bearish', or None (no clear bias).
    Requires at least 2 of 3 conditions to agree.
    Conditions:
      1. Price vs VWAP
      2. EMA 9 vs EMA 21 cross
      3. Price vs ORB high/low
    """
    signals = []

    # 1. VWAP
    if price > vwap * 1.001:
        signals.append("bullish")
    elif price < vwap * 0.999:
        signals.append("bearish")

    # 2. EMA cross
    if ema9 > ema21:
        signals.append("bullish")
    elif ema9 < ema21:
        signals.append("bearish")

    # 3. ORB
    if price > orb["high"]:
        signals.append("bullish")
    elif price < orb["low"]:
        signals.append("bearish")

    bull = signals.count("bullish")
    bear = signals.count("bearish")

    if bull >= 2: return "bullish"
    if bear >= 2: return "bearish"
    return None


def run_all(df: pd.DataFrame) -> dict:
    """
    Run all indicators on a 1-min bar DataFrame.
    Returns a flat dict of latest values ready for strategy.py.

    df must have: open, high, low, close, volume
    indexed by datetime, sorted ascending.
    """
    if df.empty or len(df) < 5:
        return {}

    close  = df["close"]
    latest = close.iloc[-1]

    vwap_series = compute_vwap(df)
    ema9_series  = compute_ema(close, 9)
    ema21_series = compute_ema(close, 21)
    rsi_series   = compute_rsi(close, 14)
    macd_df      = compute_macd(close)

    vwap  = float(vwap_series.iloc[-1])
    ema9  = float(ema9_series.iloc[-1])
    ema21 = float(ema21_series.iloc[-1])
    rsi   = float(rsi_series.iloc[-1]) if not rsi_series.isna().all() else 50.0
    macd_hist = float(macd_df["histogram"].iloc[-1]) \
                if not macd_df["histogram"].isna().all() else 0.0

    orb = compute_orb(df, minutes=15)
    direction = get_direction_bias(latest, vwap, ema9, ema21, orb)

    return {
        "price":     round(latest, 4),
        "vwap":      round(vwap, 4),
        "ema9":      round(ema9, 4),
        "ema21":     round(ema21, 4),
        "rsi":       round(rsi, 2),
        "macd_hist": round(macd_hist, 4),
        "orb_high":  round(orb["high"], 4),
        "orb_low":   round(orb["low"], 4),
        "direction": direction,
    }
