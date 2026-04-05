"""
signals/sizing.py
Stop loss and profit target calculator for credit and debit spreads.
"""

from config.thresholds import STOP_CREDIT_MULT, STOP_DEBIT_PCT, PROFIT_TARGET_PCT


def credit_exits(credit_received: float) -> dict:
    """
    Credit spread exit levels.
      Stop:   close when spread value reaches 2x credit received.
      Target: close at 50% of max profit (keep half the premium).
    """
    stop_at   = round(credit_received * STOP_CREDIT_MULT, 2)
    target_at = round(credit_received * (1 - PROFIT_TARGET_PCT), 2)
    return {
        "stop_debit":    stop_at,
        "profit_target": target_at,
        "stop_note":     f"Close if spread reaches ${stop_at:.2f} debit",
        "target_note":   f"Take profit at ${target_at:.2f} credit remaining (50% of max)",
    }


def debit_exits(debit_paid: float) -> dict:
    """
    Debit spread exit levels.
      Stop:   close at -50% of premium paid.
      Target: close at +50% gain on debit paid.
    """
    stop_at   = round(debit_paid * (1 - STOP_DEBIT_PCT), 2)
    target_at = round(debit_paid * (1 + PROFIT_TARGET_PCT), 2)
    return {
        "stop_value":    stop_at,
        "profit_target": target_at,
        "stop_note":     f"Close if spread falls to ${stop_at:.2f} value",
        "target_note":   f"Take profit if spread reaches ${target_at:.2f} value",
    }
