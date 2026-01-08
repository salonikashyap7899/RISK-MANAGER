# config.py
# 1. Binance Credentials (Never share these!)

BINANCE_KEY =  'iyK0QCtq44CZb7K5BlRcZPCrjn2i7zeL52KQXxs9654NWkQnfQIvm1rKBaNhbXob'
BINANCE_SECRET = 'EowxqqSJr8vD15Bk8oUGArIn9TrYaXlmPjoccV7TVLqLFZ7aqId3KzJY9l5iurOp'
# Trading Configuration
MAX_TRADES_PER_DAY = 4
MAX_TRADES_PER_SYMBOL_PER_DAY = 2

# Risk Management
MAX_RISK_PERCENT = 1.0  # 1% risk per trade
SL_EDIT_MIN_PERCENT = -1.0  # Minimum SL adjustment
SL_EDIT_MAX_PERCENT = 0.0   # Maximum SL adjustment

# Update Intervals (seconds)
POSITION_UPDATE_INTERVAL = 3
PRICE_UPDATE_INTERVAL = 5
