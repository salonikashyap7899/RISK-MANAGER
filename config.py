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
