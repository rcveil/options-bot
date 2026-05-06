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


def jade_lizard_exits(total_credit: float) -> dict:
    """
    Jade lizard exit levels.
      Stop:   close entire position if total debit to close reaches 2x credit.
              Put loss is the primary risk — close before it escalates.
      Target: close at 50% of total credit received.

    Note: no upside stop needed — call spread width is covered by total credit.
    Only downside (put side) requires stop management.
    """
    stop_at   = round(total_credit * STOP_CREDIT_MULT, 2)
    target_at = round(total_credit * (1 - PROFIT_TARGET_PCT), 2)
    return {
        "stop_debit":    stop_at,
        "profit_target": target_at,
        "stop_note":     (
            f"Close all 3 legs if total debit to close reaches ${stop_at:.2f}. "
            f"No upside stop needed — call spread is fully covered by credit."
        ),
        "target_note":   (
            f"Take profit at ${target_at:.2f} total credit remaining (50% of max)"
        ),
    }
