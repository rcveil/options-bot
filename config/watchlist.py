WATCHLIST = {
    "semicon": ["MU", "AVGO", "STX", "AMD", "NVDA"],
    "metals":  ["GLD", "SLV"],
    "tech":    ["MSFT", "GOOGL", "AMZN", "AAPL"],
    "index":   ["SPX", "SPY", "QQQ"],
}

ALL_SYMBOLS  = [s for group in WATCHLIST.values() for s in group]
INDEX_ONLY   = ["SPX", "SPY", "QQQ"]   # VIX spike whitelist
