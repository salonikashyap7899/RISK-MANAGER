# Binance Credentials - IMPORTANT: Keep these secure!

BINANCE_KEY = 'iyK0QCtq44CZb7K5BlRcZPCrjn2i7zeL52KQXxs9654NWkQnfQIvm1rKBaNhbXob'
BINANCE_SECRET = 'EowxqqSJr8vD15Bk8oUGArIn9TrYaXlmPjoccV7TVLqLFZ7aqId3KzJY9l5iurOp'

# Trading Configuration
MAX_TRADES_PER_DAY = 4
MAX_TRADES_PER_SYMBOL_PER_DAY = 2  # Maximum 2 trades per symbol per day

# Risk Management
MAX_RISK_PERCENT = 1.0  # 1% risk per trade
SL_EDIT_MIN_PERCENT = -1.0  # Minimum SL adjustment (can move SL up to -1%)
SL_EDIT_MAX_PERCENT = 0.0   # Maximum SL adjustment (cannot move beyond entry)

# Update Intervals (seconds)
POSITION_UPDATE_INTERVAL = 3
PRICE_UPDATE_INTERVAL = 5

# API Rate Limiting Protection
PRICE_CACHE_DURATION = 5  # Cache prices for 5 seconds
SYMBOL_CACHE_DURATION = 3600  # Cache symbols for 1 hour
MAX_RETRIES = 3  # Retry failed API calls
RETRY_DELAY = 1  # Delay between retries in seconds
