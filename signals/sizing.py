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


def butterfly_exits(debit_paid: float, max_profit: float) -> dict:
    """
    Butterfly exit levels.
      Stop:   close at -50% of debit paid (lose half the lottery ticket cost).
      Target: close at 25% of max profit — butterflies rarely pin perfectly,
              take partial profit early.
    """
    stop_at   = round(debit_paid * 0.50, 2)
    target_at = round(max_profit * 0.25, 2)
    return {
        "stop_value":    stop_at,
        "profit_target": target_at,
        "stop_note":     f"Close if value drops to ${stop_at:.2f} (−50% of debit)",
        "target_note":   f"Take profit at ${target_at:.2f} (25% of max profit)",
    }
