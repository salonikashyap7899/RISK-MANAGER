# For Development/Testing - Remove these in production!
import os

# This pulls the keys you typed into the Render Dashboard
BINANCE_KEY = os.getenv('BINANCE_KEY')
BINANCE_SECRET = os.getenv('BINANCE_SECRET')
PROXY_URL = os.getenv('PROXY_URL')

# Troubleshooting print (Check your Render logs to see this)
if not BINANCE_KEY or not BINANCE_SECRET:
    print("⚠️ WARNING: Binance Keys missing from Environment!")
else:
    print(f"✅ Config loaded. Proxy status: {'Enabled' if PROXY_URL else 'Disabled'}")

# Razorpay Configuration
RAZORPAY_KEY_ID = os.getenv('RAZORPAY_KEY_ID', 'rzp_live_SK0QFnXQv9Ed4b') 
RAZORPAY_KEY_SECRET = os.getenv('RAZORPAY_KEY_SECRET',  'y5QeUePyOVDeqN0fGOeH6FSo')

# Correct Plan IDs
RAZORPAY_MONTHLY_PLAN_ID = os.getenv('RAZORPAY_MONTHLY_PLAN_ID', "plan_SK10d2Fpo8noaR")
RAZORPAY_YEARLY_PLAN_ID = os.getenv('RAZORPAY_YEARLY_PLAN_ID', "plan_SK12UnaDI5gGcd")

# Google OAuth — set these in your environment (Render dashboard / .env).
# Never hardcode the client secret here: it gets committed to git.
GOOGLE_CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID', '839932973310-n309g8i0p66c6akaila1fvbshgcrqqko.apps.googleusercontent.com')
GOOGLE_CLIENT_SECRET = os.getenv('GOOGLE_CLIENT_SECRET', '')



# Trading Configuration
RISK_PER_TRADE = 1.0  # Hard 1% risk per trade
MAX_DAILY_TRADES = 4
MAX_SYMBOL_TRADES = 2
MAX_TRADES_PER_DAY = 4
MAX_TRADES_PER_SYMBOL_PER_DAY = 2
MAX_TRAIL_LOSS_PCT = 1.0  # Trailing SL max loss from entry
MAX_RISK_PERCENT = 1.0  # Legacy
SL_EDIT_MIN_PERCENT = -1.0
SL_EDIT_MAX_PERCENT = 0.0
POSITION_UPDATE_INTERVAL = 3
PRICE_UPDATE_INTERVAL = 5
PRICE_CACHE_DURATION = 5
SYMBOL_CACHE_DURATION = 600  # Reduced for fresher symbols (logic.py effective 10min)
MAX_RETRIES = 3
RETRY_DELAY = 1


# Binance Error Code Mappings
BINANCE_ERROR_CODES = {
    -2015: {
        'title': 'Invalid API Key / IP / Permissions',
        'message': '1. Verify API key/secret correct\\n2. Enable "Futures" permissions\\n3. Whitelist server IP (Google Cloud)\\n4. Regenerate key if needed',
        'code': -2015
    },
    -1021: {
        'title': 'Timestamp out of sync (FIXED)',
        'message': '✅ Auto-fixed with server time sync (offset applied). If persists: 1. Windows Settings→Time→"Sync now" 2. Check internet/VPN 3. Refresh page.',
        'code': -1021
    },
    -2010: {
        'title': 'New requests too frequent',
        'message': 'Rate limited. Wait 1-2 minutes.',
        'code': -2010
    },
    -1100: {
        'title': 'Illegal characters',
        'message': 'Invalid characters in params.',
        'code': -1100
    }
}

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

# ==========================
# Testing / Admin utilities
# ==========================
# When enabled, the dashboard shows test-only controls like resetting daily limits.
TESTING_MODE = os.getenv('TESTING_MODE', 'false').strip().lower() in ('1', 'true', 'yes', 'on')

# Virtual Guard Settings
VIRTUAL_GUARD_INTERVAL_SECONDS = 1.0
