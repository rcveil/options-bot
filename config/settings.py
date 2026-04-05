import os
from dotenv import load_dotenv

load_dotenv()

TASTYTRADE_USERNAME = os.getenv("TASTYTRADE_USERNAME")
TASTYTRADE_PASSWORD = os.getenv("TASTYTRADE_PASSWORD")
TELEGRAM_BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID    = int(os.getenv("TELEGRAM_CHAT_ID", "0"))
ACCOUNT_SIZE        = float(os.getenv("ACCOUNT_SIZE", "75000"))

# Trading window (ET)
MARKET_OPEN_ET  = "09:30"
MARKET_CLOSE_ET = "10:30"
TIMEZONE        = "America/New_York"
