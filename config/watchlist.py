WATCHLIST = {
    "semicon": ["MU", "AVGO", "STX", "AMD", "AMAT", "MRVL", "LRCX", "SNDK", "ARM"],
    "tech":    ["MSFT", "GOOGL", "AMZN", "AAPL", "ORCL"],
    "optical": ["LITE", "AAOI", "COHR"],
    "space":   ["SPCX"],
    "index":   ["SPX"],
}

ALL_SYMBOLS  = [s for group in WATCHLIST.values() for s in group]
INDEX_ONLY   = ["SPX"]   # VIX spike whitelist
