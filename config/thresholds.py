# IVR zones
IVR_SELL_MIN   = 50    # sell premium above this
IVR_BUY_MAX    = 30    # buy premium below this

# Entry quality gates
MIN_CREDIT_WIDTH_RATIO = 0.30   # collect >= 30% of spread width
MIN_POP_CREDIT         = 0.65   # normal VIX
MIN_POP_ELEVATED       = 0.70   # VIX 18-28
MAX_BID_ASK_PCT        = 0.10   # max 10% of mid price
MIN_OPEN_INTEREST      = 500
MIN_OPEN_INTEREST_SPX  = 200

# Delta targets
DELTA_SHORT_CREDIT_MIN = 0.20
DELTA_SHORT_CREDIT_MAX = 0.35
DELTA_LONG_DEBIT_MIN   = 0.40
DELTA_LONG_DEBIT_MAX   = 0.55

# DTE targets
DTE_CREDIT_MIN = 21
DTE_CREDIT_MAX = 45
DTE_DEBIT_MIN  = 7
DTE_DEBIT_MAX  = 21

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
