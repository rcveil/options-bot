# IVR zones
IVR_SELL_MIN   = 50    # sell premium above this
IVR_BUY_MAX    = 30    # buy premium below this

# Entry quality gates
MIN_CREDIT_WIDTH_RATIO    = 0.25   # collect >= 25% of spread width (was 30%)
MIN_POP_CREDIT            = 0.60   # normal VIX (was 65%)
MIN_POP_ELEVATED          = 0.65   # VIX 18-28 (was 70%)
MIN_POP_IC_COMBINED       = 0.40   # iron condor combined PoP (put_pop × call_pop)
MIN_POP_IC_COMBINED_ELEV  = 0.45   # IC combined PoP when VIX elevated
MAX_BID_ASK_PCT           = 0.10   # max 10% of mid price
MIN_OPEN_INTEREST         = 200    # per-strike minimum (was 500)
MIN_OPEN_INTEREST_SPX     = 100    # index symbols (was 200)

# Butterfly-specific
MAX_BUTTERFLY_DEBIT_RATIO  = 0.30  # debit < 30% of wing width (was 25%)
MIN_BUTTERFLY_PROFIT_RATIO = 1.5   # max_profit / debit >= 1.5x (was 2.0x)

# Delta targets
DELTA_SHORT_CREDIT_MIN = 0.15   # slightly wider range (was 0.20)
DELTA_SHORT_CREDIT_MAX = 0.35
DELTA_LONG_DEBIT_MIN   = 0.40
DELTA_LONG_DEBIT_MAX   = 0.60   # slightly wider (was 0.55)

# DTE targets
DTE_CREDIT_MIN = 28   # raised from 21 — avoids gamma risk in final 3 weeks
DTE_CREDIT_MAX = 45
DTE_DEBIT_MIN  = 7
DTE_DEBIT_MAX  = 21

# Conviction thresholds (number of indicators required to agree)
# 3-of-3 required for high-risk structures (jade lizard, iron condor, debit spreads)
# 2-of-3 sufficient for defined-risk credit spreads (bull put, bear call)
CONVICTION_HIGH  = 3   # jade_lizard, iron_condor, bull_call_spread, bear_put_spread
CONVICTION_NORMAL = 2  # bull_put_spread, bear_call_spread

# Minimum bars before first scan is valid (ensures full 15-min ORB)
MIN_BARS_FOR_SCAN = 15

# Risk management
MAX_RISK_PCT      = 0.05   # 5% account max
MIN_RISK_PCT      = 0.02   # 2% account min
STOP_CREDIT_MULT  = 2.0    # close at 2x credit received
STOP_DEBIT_PCT    = 0.50   # close at -50% of debit paid
PROFIT_TARGET_PCT = 0.50   # take profit at 50% of max

# VIX regimes
VIX_NORMAL   = 18
VIX_ELEVATED = 28
VIX_SPIKE    = 35
VIX_PAUSE    = 45
