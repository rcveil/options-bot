"""
signals/indicators.py
Computes all technical indicators from 1-min OHLCV bars.

ORB uses first 15 bars (first 15 minutes from open).
If fewer than 15 bars exist (early scan at 09:30–09:44),
ORB uses all available bars — still valid as a partial range.

All indicators operate on the full session from 09:30 ET so
VWAP anchors correctly from open regardless of scan time.

run_all() now returns conviction_count (0–3) alongside direction.
This allows main.py to enforce 3-of-3 for high-risk structures
and 2-of-3 for standard credit spreads.
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
    """
    Opening Range Breakout using the first N minutes.
    If fewer than N bars exist (early scan), uses all available bars.
    """
    orb_bars = df.iloc[:minutes] if len(df) >= minutes else df
    return {
        "high": float(orb_bars["high"].max()),
        "low":  float(orb_bars["low"].min()),
        "bars": len(orb_bars),
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
) -> tuple[Optional[str], int]:
    """
    Requires 2 of 3 conditions to agree for a directional signal.
    Returns (direction, conviction_count) where conviction_count is
    the number of indicators agreeing with the returned direction (0–3).

    conviction_count is used by main.py to enforce stricter gates:
      - 3-of-3 required for jade lizard, iron condor, debit spreads
      - 2-of-3 sufficient for bull put / bear call spreads

    Logs each condition so you can see exactly why direction is None.
    """
    signals = []

    # 1. VWAP
    if price > vwap * 1.001:
        signals.append("bullish")
        vwap_str = f"bullish (price {price:.2f} > VWAP {vwap:.2f})"
    elif price < vwap * 0.999:
        signals.append("bearish")
        vwap_str = f"bearish (price {price:.2f} < VWAP {vwap:.2f})"
    else:
        vwap_str = f"neutral (price {price:.2f} at VWAP {vwap:.2f})"

    # 2. EMA cross
    if ema9 > ema21:
        signals.append("bullish")
        ema_str = f"bullish (EMA9 {ema9:.2f} > EMA21 {ema21:.2f})"
    elif ema9 < ema21:
        signals.append("bearish")
        ema_str = f"bearish (EMA9 {ema9:.2f} < EMA21 {ema21:.2f})"
    else:
        ema_str = f"neutral (EMA9=EMA21={ema9:.2f})"

    # 3. ORB
    if price > orb["high"]:
        signals.append("bullish")
        orb_str = f"bullish (price {price:.2f} > ORB high {orb['high']:.2f})"
    elif price < orb["low"]:
        signals.append("bearish")
        orb_str = f"bearish (price {price:.2f} < ORB low {orb['low']:.2f})"
    else:
        orb_str = (
            f"neutral (price {price:.2f} inside ORB "
            f"{orb['low']:.2f}–{orb['high']:.2f}, "
            f"{orb['bars']} bars)"
        )

    bull = signals.count("bullish")
    bear = signals.count("bearish")

    if bull >= 2:
        direction       = "bullish"
        conviction_count = bull
    elif bear >= 2:
        direction        = "bearish"
        conviction_count = bear
    else:
        direction        = None
        conviction_count = max(bull, bear)

    logger.info(
        f"{symbol}: VWAP={vwap_str} | EMA={ema_str} | ORB={orb_str} "
        f"→ direction={direction} (bull={bull} bear={bear} "
        f"conviction={conviction_count}/3)"
    )

    return direction, conviction_count


def run_all(df: pd.DataFrame, symbol: str = "?") -> dict:
    if df.empty or len(df) < 5:
        logger.warning(
            f"{symbol}: only {len(df)} bars — need 5+ for indicators"
        )
        return {}

    close  = df["close"]
    latest = float(close.iloc[-1])

    vwap_s  = compute_vwap(df)
    ema9_s  = compute_ema(close, 9)
    ema21_s = compute_ema(close, 21)
    rsi_s   = compute_rsi(close, 14)
    macd_df = compute_macd(close)

    vwap      = float(vwap_s.iloc[-1])
    ema9      = float(ema9_s.iloc[-1])
    ema21     = float(ema21_s.iloc[-1])
    rsi       = float(rsi_s.iloc[-1]) if not rsi_s.isna().all() else 50.0
    macd_hist = float(macd_df["histogram"].iloc[-1]) \
                if not macd_df["histogram"].isna().all() else 0.0

    orb = compute_orb(df, minutes=15)

    direction, conviction_count = get_direction_bias(
        symbol, latest, vwap, ema9, ema21, orb
    )

    logger.info(
        f"{symbol}: price={latest:.2f} VWAP={vwap:.2f} "
        f"EMA9={ema9:.2f} EMA21={ema21:.2f} "
        f"RSI={rsi:.1f} MACD_hist={macd_hist:.4f} "
        f"ORB={orb['low']:.2f}–{orb['high']:.2f} ({orb['bars']} bars)"
    )

    return {
        "price":           round(latest,    4),
        "vwap":            round(vwap,      4),
        "ema9":            round(ema9,      4),
        "ema21":           round(ema21,     4),
        "rsi":             round(rsi,       2),
        "macd_hist":       round(macd_hist, 4),
        "orb_high":        round(orb["high"], 4),
        "orb_low":         round(orb["low"],  4),
        "orb_bars":        orb["bars"],
        "direction":       direction,
        "conviction_count": conviction_count,
    }
