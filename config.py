# # Binance Credentials - IMPORTANT: Keep these secure!

# BINANCE_KEY = 'iyK0QCtq44CZb7K5BlRcZPCrjn2i7zeL52KQXxs9654NWkQnfQIvm1rKBaNhbXob'
# BINANCE_SECRET = 'EowxqqSJr8vD15Bk8oUGArIn9TrYaXlmPjoccV7TVLqLFZ7aqId3KzJY9l5iurOp'

# # Trading Configuration
# MAX_TRADES_PER_DAY = 4
# MAX_TRADES_PER_SYMBOL_PER_DAY = 2  # Maximum 2 trades per symbol per day

# # Risk Management
# MAX_RISK_PERCENT = 1.0  # 1% risk per trade
# SL_EDIT_MIN_PERCENT = -1.0  # Minimum SL adjustment (can move SL up to -1%)
# SL_EDIT_MAX_PERCENT = 0.0   # Maximum SL adjustment (cannot move beyond entry)

# # Update Intervals (seconds)
# POSITION_UPDATE_INTERVAL = 3
# PRICE_UPDATE_INTERVAL = 5

# # API Rate Limiting Protection
# PRICE_CACHE_DURATION = 5  # Cache prices for 5 seconds
# SYMBOL_CACHE_DURATION = 3600  # Cache symbols for 1 hour
# MAX_RETRIES = 3  # Retry failed API calls
# RETRY_DELAY = 1  # Delay between retries in seconds

# # Razorpay Configuration
# RAZORPAY_KEY_ID = 'rzp_live_SK0QFnXQv9Ed4b'  # Replace with your Razorpay Key ID
# RAZORPAY_KEY_SECRET = 'y5QeUePyOVDeqN0fGOeH6FSo'  # Replace with your Razorpay Key Secret
# RAZORPAY_PLAN_ID = 'your_razorpay_plan_id'  # Replace with your Razorpay Plan ID

# # Subscription Configuration
# SUBSCRIPTION_PRICE_INR = 499  # ₹500 per month

# RAZORPAY_YEARLY_PLAN_ID="plan_SK12UnaDI5gGcd"
# RAZORPAY_MONTHLY_PLAN_ID="plan_SK10d2Fpo8noaR"


# config.py

# ⚠️ SECURITY: Using hardcoded keys is NOT recommended for production!
# Best Practice: Use Environment Variables
# Example: import os; BINANCE_KEY = os.getenv('BINANCE_KEY')

# For Development/Testing - Remove these in production!
import os

# Binance Credentials - Can be customized per user via Exchange Connection
# Default demo keys (should be removed in production)
BINANCE_KEY = os.getenv('BINANCE_KEY', '')
BINANCE_SECRET = os.getenv('BINANCE_SECRET', '')

# Razorpay Configuration
RAZORPAY_KEY_ID = os.getenv('RAZORPAY_KEY_ID', 'rzp_live_SK0QFnXQv9Ed4b') 
RAZORPAY_KEY_SECRET = os.getenv('RAZORPAY_KEY_SECRET', 'y5QeUePyOVDeqN0fGOeH6FSo')

# Correct Plan IDs
RAZORPAY_MONTHLY_PLAN_ID = os.getenv('RAZORPAY_MONTHLY_PLAN_ID', "plan_SK10d2Fpo8noaR")
RAZORPAY_YEARLY_PLAN_ID = os.getenv('RAZORPAY_YEARLY_PLAN_ID', "plan_SK12UnaDI5gGcd")

# Trading Configuration
MAX_TRADES_PER_DAY = 4
MAX_TRADES_PER_SYMBOL_PER_DAY = 2
MAX_RISK_PERCENT = 1.0
SL_EDIT_MIN_PERCENT = -1.0
SL_EDIT_MAX_PERCENT = 0.0
POSITION_UPDATE_INTERVAL = 3
PRICE_UPDATE_INTERVAL = 5
PRICE_CACHE_DURATION = 5
SYMBOL_CACHE_DURATION = 3600
MAX_RETRIES = 3
RETRY_DELAY = 1

# Supported Exchanges for User Connections
SUPPORTED_EXCHANGES = {
    'binance': {
        'name': 'Binance Futures',
        'api_required': ['api_key', 'api_secret'],
        'description': 'Connect your Binance Futures account'
    },
    'bybit': {
        'name': 'Bybit',
        'api_required': ['api_key', 'api_secret'],
        'description': 'Connect your Bybit account'
    },
    'okx': {
        'name': 'OKX',
        'api_required': ['api_key', 'api_secret', 'passphrase'],
        'description': 'Connect your OKX account'
    },
    'metatrader': {
        'name': 'MetaTrader 4/5',
        'api_required': ['server', 'login', 'password'],
        'description': 'Connect via MetaTrader API'
    },
    'upstox': {
        'name': 'Upstox',
        'api_required': ['api_key', 'api_secret'],
        'description': 'Connect your Upstox account (India)'
    }
}
