from flask import session
from datetime import datetime
from binance.client import Client
from binance.exceptions import BinanceAPIException
import config
import math
import traceback
import time
import requests
from models import db, TradeDailyStats, TradeLog

# Global variables - Default client (for demo/fallback)
_default_client = None
_symbol_cache = None
_symbol_cache_time = 0
_price_cache = {}
_price_cache_time = {}
_positions_cache = {}
_positions_cache_time = {}
_trade_history_cache = {}
_trade_history_cache_time = {}
_leverage_cache = {}
_leverage_cache_time = {}
_last_call_time = 0
CACHE_DURATION = 5

# Known leverage limits for common coins (updated based on Binance data)
# These serve as fallback when API fails
KNOWN_LEVERAGE_MAP = {
    # Tier 1 - Most coins: 125x
    'BTCUSDT': 125, 'ETHUSDT': 75, 'BNBUSDT': 75, 'SOLUSDT': 75,
    'XRPUSDT': 75, 'ADAUSDT': 75, 'LINKUSDT': 75, 'LTCUSDT': 75,
    'DOGEUSDT': 50, 'MATICUSDT': 75, 'AVAXUSDT': 75, 'DOTUSDT': 75,
    'UNIUSDT': 75, 'APTUSDT': 75, 'FILUSDT': 75, 'TAOUSDT': 75,
    
    # Tier 2 - Medium leverage: 50x or less
    'BIOUSDT': 50,   # BIO can be 50x
    'MEMUSDT': 50, 'PEPEUSDT': 50, 'DYDXUSDT': 50,
    'OPSUSDT': 50, 'ARBUSDT': 50, 'SUIUSDT': 50,
    
    # Tier 3 - Low leverage coins: 20-25x
    'BANKUSDT': 20,  # Bank max 20x
    'SOLSOL': 20,    # Solana perp max 20x
    'MEMEUSDT': 25,
    
    # Defaults if needed
    'DEFAULT': 125,
}

# User-specific client storage
_user_clients = {}

def get_user_exchange_client(user_id):
    """
    Get Binance client for a specific user based on their connected exchange.
    Returns the user's own API keys if connected, otherwise None.
    """
    from models import ExchangeConnection
    import config  

    # Check if we already have a cached client for this user
    if user_id in _user_clients:
        return _user_clients[user_id]
    
    # Get user's exchange connection from database
    connection = ExchangeConnection.query.filter_by(
        user_id=user_id, 
        exchange_type='binance',
        is_connected=True
    ).first()
    
    if not connection or not connection.api_key or not connection.api_secret:
        return None
    
    try:
        # Sync timestamp BEFORE client creation
        time_offset = sync_time_with_binance()
        
        # CRITICAL FIX: Properly bundle parameters into requests_params
        req_params = {'timeout': 20}
        
        # Proxy support for geo-restrictions
        if hasattr(config, 'PROXY_URL') and config.PROXY_URL:
            req_params['proxies'] = {
                'https': config.PROXY_URL, 
                'http': config.PROXY_URL
            }
            print(f"🌐 Using proxy: {config.PROXY_URL}")
        
        # Create client safely using requests_params
        client = Client(
            api_key=connection.api_key,
            api_secret=connection.api_secret,
            requests_params=req_params
        )
        
        # Apply timestamp offset (PERMANENT -1021 FIX)
        if abs(time_offset) > 100:
            client.timestamp_offset = time_offset
            print(f"✅ Applied user client offset: {time_offset}ms")
        
        # Verify the connection works with synced time
        client.futures_account(recvWindow=10000)
        
        print(f"✅ User {user_id} Binance client created successfully")
        # Cache the client
        _user_clients[user_id] = client
        return client
        
    except BinanceAPIException as e:
        error_info = config.BINANCE_ERROR_CODES.get(e.code)
        print(f"❌ BinanceAPIException for user {user_id}: code={e.code}, {error_info['title'] if error_info else str(e)}")
        connection.is_connected = False
        from models import db
        db.session.commit()
        return None
        
    except Exception as e:
        print(f"❌ Unexpected error creating client for user {user_id}: {e}")
        connection.is_connected = False
        from models import db
        db.session.commit()
        return None

def set_user_client(user_id, client):
    """Manually set the client for a user (for testing)"""
    _user_clients[user_id] = client

def clear_user_client(user_id):
    """Clear cached client for a user (when they disconnect)"""
    if user_id in _user_clients:
        del _user_clients[user_id]

def sync_time_with_binance():
    """Sync local time with Binance server time - ROBUST VERSION"""
    import config
    endpoints = [
        'https://fapi.binance.com/fapi/v1/time',
        'https://fapi.binance.com/fapi/v2/time', 
        'https://api.binance.com/api/v3/time'
    ]
    
    for endpoint in endpoints:
        try:
            proxies = {}
            if hasattr(config, 'PROXY_URL') and config.PROXY_URL:
                proxies = {'https': config.PROXY_URL, 'http': config.PROXY_URL}
            
            response = requests.get(endpoint, timeout=5, proxies=proxies)
            server_time = int(response.json().get('serverTime', 0))
            if server_time > 0:
                local_time = int(time.time() * 1000)
                offset = server_time - local_time
                print(f"⏰ Synced with {endpoint}: offset={offset}ms")
                return offset
        except Exception as e:
            print(f"⚠️ {endpoint} failed: {e}")
            continue
    
    print("⚠️ All time sync endpoints failed - using 0 offset (may cause -1021)")
    return 0

def get_client(user_id=None):
    """
    Get or create Binance client with error handling.
    If user_id is provided, tries to use user's connected exchange.
    Otherwise uses default config.
    """
    global _default_client
    import config 
    
    # If user_id provided, try to get user's own exchange
    if user_id:
        user_client = get_user_exchange_client(user_id)
        if user_client:
            return user_client
    
    # Fallback to default client
    if _default_client is None:
        try:
            if config.BINANCE_KEY and config.BINANCE_SECRET and len(config.BINANCE_KEY) > 5:
                time_offset = sync_time_with_binance()
                
                # CRITICAL FIX: Properly bundle parameters into requests_params
                req_params = {'timeout': 20}
                
                if hasattr(config, 'PROXY_URL') and config.PROXY_URL:
                    req_params['proxies'] = {
                        'https': config.PROXY_URL, 
                        'http': config.PROXY_URL
                    }
                
                _default_client = Client(
                    api_key=config.BINANCE_KEY, 
                    api_secret=config.BINANCE_SECRET,
                    requests_params=req_params
                )
                
                if abs(time_offset) > 100:  
                    _default_client.timestamp_offset = time_offset
                
                _default_client.futures_account(recvWindow=10000)
                print("✅ Default Binance client initialized successfully")
            else:
                return None
        except Exception as e:
            print(f"❌ Error initializing default Binance client: {e}")
            _default_client = None
            return None
    
    return _default_client

def initialize_session():
    """Initialize session variables"""
    if "trades" not in session:
        session["trades"] = []
    if "stats" not in session:
        session["stats"] = {}
    session.modified = True

def get_all_exchange_symbols(user_id=None):
    """Fetches ALL USDT trading symbols from Binance Futures"""
    global _symbol_cache, _symbol_cache_time
    now = time.time()
    

    try:
        client = get_client(user_id)
        if not client:
            raise Exception("Binance client not initialized")
        
        info = client.futures_exchange_info()
        
        symbols = sorted([
            s['symbol'] for s in info.get('symbols', []) 
            if s['status'] == 'TRADING' 
            and s['quoteAsset'] == 'USDT'
            and s['contractType'] == 'PERPETUAL'
        ])
        
        if len(symbols) > 0:
            _symbol_cache = symbols
            _symbol_cache_time = now
            return symbols
            
    except Exception as e:
        print(f"⚠️ Symbol Fetch Error: {e}")
        # Use cached symbols if available, otherwise return fallback
        if _symbol_cache:
            return _symbol_cache
        
    # Fallback: return minimal symbol set
    return ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"]
def validate_symbol(client, symbol):
    try:
        # Check if it's in the exchange info
        info = client.get_symbol_info(symbol.upper())
        return info is not None
    except:
        return False

def get_wallet_balances(user_id=None):
    """
    Get wallet balances (FUTURES WALLET ONLY)
    Returns ONLY assets with total > 0
    All numeric values safely converted from string → float
    """
    try:
        from models import ExchangeConnection

        connection = ExchangeConnection.query.filter_by(
            user_id=user_id,
            exchange_type='binance',
            is_connected=True
        ).first()

        if not connection:
            return {
                'success': False,
                'error': 'No Binance connection found',
                'balances': [],
                'total_assets': 0
            }

        client = get_client(user_id)

        if client is None:
            return {
                'success': False,
                'error': 'Client not initialized',
                'balances': [],
                'total_assets': 0
            }

        # ✅ CORRECT ENDPOINT (FUTURES)
        account = client.futures_account(recvWindow=10000)

        assets = account.get('assets', [])
        balances = []
        total_usdt_equiv = 0.0

        for asset in assets:
            try:
                asset_name = asset.get('asset', '')

                # ✅ SAFE FLOAT CONVERSION
                wallet_balance = float(asset.get('walletBalance', '0'))
                available_balance = float(asset.get('availableBalance', '0'))

                if wallet_balance <= 0:
                    continue  # ✅ ONLY RETURN NON-ZERO

                balances.append({
                    'asset': asset_name,
                    'free': round(available_balance, 6),
                    'locked': round(wallet_balance - available_balance, 6),
                    'total': round(wallet_balance, 6)
                })

                # ✅ USDT VALUE CALCULATION
                if asset_name == 'USDT':
                    total_usdt_equiv += wallet_balance
                else:
                    price = get_live_price(f"{asset_name}USDT", user_id)
                    if price:
                        total_usdt_equiv += wallet_balance * float(price)

            except Exception as inner_err:
                print(f"⚠️ Asset parse error: {inner_err}")
                continue

        return {
            'success': True,
            'balances': balances,
            'total_assets': len(balances),
            'total_usdt_equiv': round(total_usdt_equiv, 2)
        }

    except Exception as e:
        print(f"❌ WALLET ERROR: {e}")
        traceback.print_exc()
        return {
            'success': False,
            'error': str(e),
            'balances': [],
            'total_assets': 0
        }

def get_entry_price(symbol, user_id=None):
    """Get entry price safely parsing strings to floats"""
    try:
        client = get_client(user_id)
        if client is None:
            return {'success': False, 'error': 'No Binance client available'}
        
        trades = client.futures_account_trades(symbol=symbol, limit=1000)
        
        if not trades:
            positions = client.futures_position_information(symbol=symbol)
            for pos in positions:
                if float(pos.get('positionAmt', 0)) != 0:
                    return {
                        'success': True, 
                        'entry_price': float(pos.get('entryPrice', 0)),
                        'trades_used': 0,
                        'method': 'position_entryPrice_fallback'
                    }
            return {'success': False, 'error': 'No trades or open position'}
        
        total_qty = 0.0
        total_cost = 0.0
        
        for trade in trades:
            qty = abs(float(trade.get('qty', 0)))
            price = float(trade.get('price', 0))
            total_qty += qty
            total_cost += qty * price
        
        avg_price = total_cost / total_qty if total_qty > 0 else 0.0
        
        return {
            'success': True,
            'entry_price': round(avg_price, 6),
            'trades_used': len([t for t in trades if float(t.get('qty', 0)) != 0]),
            'total_qty': round(total_qty, 6),
            'method': 'weighted_avg_futures_account_trades'
        }
    except Exception as e:
        return {'success': False, 'error': str(e)}

def get_live_balance(user_id=None):
    """Get live wallet balance safely converting string to float"""
    try:
        client = get_client(user_id)
        if client is None: 
            return None, None
        
        acc = client.futures_account(recvWindow=10000)
        total_balance = float(acc.get("totalWalletBalance", 0))
        total_margin = float(acc.get("totalInitialMargin", 0))
        wallet_data = get_wallet_balances(user_id)
        
        return (
            total_balance, 
            total_margin
        ), {
            'success': True,
            'total_balance': total_balance,
            'total_margin': total_margin,
            'unutilized': max(total_balance - total_margin, 0),
            'wallet': wallet_data
        }
        
    except Exception as e:
        print(f"Error getting balance (user_id={user_id}): {e}")
        return None, None

def get_live_price(symbol, user_id=None):
    """
    ✅ FIXED: Bulletproof price fetch with per-symbol caching.
    CRITICAL: No cross-symbol fallback - only fetch the EXACT symbol requested.
    """
    global _price_cache, _price_cache_time
    current_time = time.time()
    
    # ✅ CRITICAL: Strip whitespace and validate symbol format
    symbol = (symbol or '').strip().upper()
    if not symbol or len(symbol) < 6 or not symbol.isalnum():
        print(f"⚠️ INVALID SYMBOL FORMAT: '{symbol}' - returning 0 (NO FALLBACK)")
        return 0.0
    
    # ✅ CRITICAL: Per-symbol, per-user cache key to prevent symbol mixing
    cache_key = f"{symbol}_{user_id or 'public'}"
    
    # ✅ Check cache with per-symbol expiration (10 second TTL)
    if cache_key in _price_cache and cache_key in _price_cache_time:
        cache_age = current_time - _price_cache_time[cache_key]
        if cache_age < 10:
            cached_price = _price_cache[cache_key]
            print(f"✓ PRICE CACHE HIT [{cache_age:.1f}s old]: {symbol} = ${cached_price}")
            return cached_price
        else:
            print(f"🔄 PRICE CACHE EXPIRED [{cache_age:.1f}s]: {symbol} - fetching fresh...")
    else:
        print(f"🔄 PRICE CACHE MISS: {symbol} - fetching fresh...")
    
    # ✅ ATTEMPT 1: Try client API (if user has connected exchange)
    try:
        client = get_client(user_id)
        if client:
            print(f"   → Trying client API for {symbol}...")
            ticker = client.futures_symbol_ticker(symbol=symbol)
            price = float(ticker.get('price', 0))
            if price > 0:
                _price_cache[cache_key] = price
                _price_cache_time[cache_key] = current_time
                print(f"✅ GOT PRICE FROM CLIENT API: {symbol} = ${price}")
                return price
            else:
                print(f"   ⚠️ Client API returned invalid price for {symbol}")
    except Exception as e:
        print(f"   ⚠️ Client API failed for {symbol}: {type(e).__name__}: {e}")
    
    # ✅ ATTEMPT 2: Try public Binance API endpoints
    public_endpoints = [
        f"https://fapi.binance.com/fapi/v1/ticker/price?symbol={symbol}",
        f"https://fapi.binance.com/fapi/v2/ticker/price?symbol={symbol}"
    ]
    proxies = {}
    if hasattr(config, 'PROXY_URL') and config.PROXY_URL:
        proxies = {'https': config.PROXY_URL, 'http': config.PROXY_URL}
        print(f"   → Using proxy: {config.PROXY_URL}")
    
    for url in public_endpoints:
        try:
            print(f"   → Trying public API: {url}")
            resp = requests.get(url, timeout=2, proxies=proxies)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, dict):
                    price_key = data.get('price')
                    if price_key:
                        price = float(price_key)
                        if price > 0:
                            _price_cache[cache_key] = price
                            _price_cache_time[cache_key] = current_time
                            print(f"✅ GOT PRICE FROM PUBLIC API: {symbol} = ${price}")
                            return price
                        else:
                            print(f"   ⚠️ Public API returned invalid price for {symbol}")
                    else:
                        print(f"   ⚠️ No 'price' field in response for {symbol}")
                else:
                    print(f"   ⚠️ Unexpected response format for {symbol}")
            else:
                print(f"   ⚠️ HTTP {resp.status_code} for {symbol}")
        except Exception as e:
            print(f"   ⚠️ Public API request failed for {symbol}: {type(e).__name__}: {e}")
            continue
    
    # ✅ LAST RESORT: Use stale cache if available (but only for this symbol)
    if cache_key in _price_cache:
        stale_price = _price_cache[cache_key]
        stale_age = current_time - _price_cache_time.get(cache_key, 0)
        print(f"⚠️ NO FRESH PRICE AVAILABLE: Using STALE cache [{stale_age:.1f}s old] for {symbol} = ${stale_price}")
        return stale_price
    
    # ✅ FINAL FALLBACK: Return 0 (NOT a wrong symbol price)
    print(f"❌ CRITICAL: NO PRICE AVAILABLE FOR {symbol} (not in cache, API failed) - returning 0")
    return 0.0

def get_symbol_filters(symbol, user_id=None):
    DEFAULT_FILTERS = [
        {'filterType': 'PRICE_FILTER', 'tickSize': '0.01'},
        {'filterType': 'LOT_SIZE', 'stepSize': '0.001', 'minQty': '0.001'},
        {'filterType': 'MIN_NOTIONAL', 'minNotional': '5'}
    ]
    try:
        client = get_client(user_id)
        if client:
            info = client.futures_exchange_info()
            for s in info.get("symbols", []):
                if s.get("symbol") == symbol: 
                    return s.get("filters", DEFAULT_FILTERS)
    except Exception:
        pass
    return DEFAULT_FILTERS

def get_min_qty(symbol, user_id=None):
    for f in get_symbol_filters(symbol, user_id):
        if f.get("filterType") == "LOT_SIZE":
            return float(f.get("minQty", f.get("stepSize", 0.001)))
    return 0.001

def get_min_notional(symbol, user_id=None):
    for f in get_symbol_filters(symbol, user_id):
        if f.get("filterType") in ["MIN_NOTIONAL", "NOTIONAL"]:
            return float(f.get("minNotional", f.get("notional", 5)))
    return 5.0

def get_required_order_qty(symbol, price, user_id=None):
    step = get_lot_step(symbol, user_id)
    if step <= 0:
        step = 0.001
    min_qty = get_min_qty(symbol, user_id)
    min_notional = get_min_notional(symbol, user_id)
    if price <= 0:
        return min_qty
    min_notional_qty = math.ceil((min_notional / price) / step) * step
    return max(min_qty, min_notional_qty)

def get_lot_step(symbol, user_id=None):
    for f in get_symbol_filters(symbol, user_id):
        if f.get("filterType") == "LOT_SIZE": 
            return float(f.get("stepSize", 0.001))
    return 0.001

def round_qty(symbol, qty, user_id=None):
    if qty <= 0:
        return 0
    step = get_lot_step(symbol, user_id)
    if step == 0: 
        step = 0.001
    precision = abs(int(round(-math.log10(step))))
    rounded = math.floor(qty / step) * step
    return round(rounded, precision) if rounded > 0 else 0

def round_price(symbol, price, user_id=None):
    for f in get_symbol_filters(symbol, user_id):
        if f.get("filterType") == "PRICE_FILTER":
            tick = float(f.get("tickSize", 0.01))
            if tick == 0: 
                return price
            precision = abs(int(round(-math.log10(tick))))
            return round(price - (price % tick), precision)
    return round(price, 2)

# NEW: Fetch maximum leverage allowed by Binance for a specific symbol
def get_max_leverage(symbol, user_id=None):
    """
    Fetches max leverage: Cache -> Live Binance API -> Known Map -> Safe Default
    """
    global _leverage_cache, _leverage_cache_time
    now = time.time()
    cache_key = f"{symbol}_{user_id or 'public'}"
    
    # 1. Check Cache (Return immediately if we already fetched it recently)
    if cache_key in _leverage_cache and (now - _leverage_cache_time.get(cache_key, 0)) < 300:
        return _leverage_cache[cache_key]
    
    # 2. Fetch LIVE data from Binance (This is the dynamic fix you need)
    try:
        client = get_client(user_id)
        if client:
            # We call the Bracket API to get the TRUE limit for this specific coin
            brackets = client.futures_leverage_bracket(symbol=symbol)
            if brackets and isinstance(brackets, list):
                max_lev = int(brackets[0]['brackets'][0]['initialLeverage'])
                
                # Save to cache and return the real number (e.g., 20, 50, or 125)
                _leverage_cache[cache_key] = max_lev
                _leverage_cache_time[cache_key] = now
                print(f"✅ {symbol} Live Max: {max_lev}x")
                return max_lev
    except Exception as e:
        print(f"⚠️ Binance API failed for {symbol}: {e}")

    # 3. Fallback to Known Map (If API fails or no keys connected)
    if symbol in KNOWN_LEVERAGE_MAP:
        max_lev = KNOWN_LEVERAGE_MAP[symbol]
        print(f"📋 {symbol} using Known Map: {max_lev}x")
        return max_lev

    # 4. Smart Safety Fallback (Preventing the 125x error)
    if symbol.endswith('USDT'):
        # Only allow 125x for the biggest, safest coins
        if any(symbol.startswith(m) for m in ['BTC', 'ETH', 'BNB']):
            return 125
        
        # For ALL other altcoins, default to 20x. 
        # This prevents the "Leverage too high" error you were getting.
        print(f"⚠️ {symbol} unknown, defaulting to safe 20x")
        return 20
        
    # 5. Absolute Final Fallback
    return 20

def calculate_position_sizing(unutilized_margin, entry, sl_type, sl_value, side="LONG", user_id=None, symbol=None):
    import config
    if entry <= 0:
        return {"error": "Invalid Entry Price"}

    if sl_value <= 0:
        return {"error": "SL is MANDATORY - cannot trade without SL"}

    # STRICT 1% RISK
    risk_amount = unutilized_margin * (config.RISK_PER_TRADE / 100.0)

    if sl_type == "SL % Movement":
        sl_percent = sl_value
        sl_distance = abs(entry * (sl_value / 100.0))
    else:
        if side == "LONG" and sl_value >= entry:
            return {"error": "LONG SL must be < entry"}
        if side == "SHORT" and sl_value <= entry:
            return {"error": "SHORT SL must be > entry"}
        sl_distance = abs(entry - sl_value)
        sl_percent = (sl_distance / entry) * 100.0

    if sl_distance <= 0:
        return {"error": "Invalid SL (0 distance)"}

    calculated_leverage = 100.0 / (sl_percent + 0.2)
    
    # NEW: Get actual Binance max leverage for the specific symbol
    # If symbol not provided, use BTCUSDT as default
    symbol_for_lev = symbol if symbol else 'BTCUSDT'
    exchange_max_lev = get_max_leverage(symbol_for_lev, user_id=user_id)
    
    # FINAL: Cap by risk-calc, exchange limit, and absolute max
    final_max_leverage = min(int(calculated_leverage), exchange_max_lev, 125)
    
    pos_value_usdt = risk_amount / ((sl_percent / 100.0) + 0.002)
    position_size = pos_value_usdt / entry

    return {
        "suggested_units": round_qty(symbol_for_lev, position_size),  # Use proper symbol
        "suggested_leverage": final_max_leverage,
        "max_leverage": final_max_leverage,
        "exchange_max_leverage": exchange_max_lev,  # Show max available on Binance
        "symbol": symbol_for_lev,  # NEW: Include the symbol in response
        "leverage_breakdown": {  # Debug info showing leverage calculation
            "risk_based": int(calculated_leverage),
            "exchange_max": exchange_max_lev,
            "final": final_max_leverage
        },
        "suggested_position_value": round(pos_value_usdt, 2),
        "risk_amount": round(risk_amount, 2),
        "sl_percent": round(sl_percent, 3),
        "sl_distance": round(sl_distance, 6),
        "risk_pct": config.RISK_PER_TRADE,
        "error": None
    }

def get_open_positions(user_id=None):
    global _positions_cache, _positions_cache_time
    current_time = time.time()
    cache_key = f"positions_{user_id or 'public'}"
    
    # Return cached positions if less than 5 seconds old (for LIVE liquidation price updates)
    # Reduced from 30s to 5s for real-time liquidation price data
    if cache_key in _positions_cache and (current_time - _positions_cache_time.get(cache_key, 0)) < 5:
        return _positions_cache[cache_key]
    
    try:
        client = get_client(user_id)
        if client is None: 
            return []
        
        positions = client.futures_position_information(recvWindow=10000)
        open_positions = []
        
        for pos in positions:
            position_amt = float(pos.get('positionAmt', 0))
            if abs(position_amt) > 0:
                entry_price = float(pos.get('entryPrice', 0))
                mark_price = float(pos.get('markPrice', 0))
                unrealized_pnl = float(pos.get('unRealizedProfit', 0))
                liquidation_price = float(pos.get('liquidationPrice', 0))
                leverage = int(pos.get('leverage', 1))
                notional = float(pos.get('notional', 0))
                
                initial_margin = abs(notional) / leverage if leverage > 0 else abs(notional)
                # ROI percent (Binance-style, based on margin used)
                roi_percent = (unrealized_pnl / initial_margin * 100) if initial_margin > 0 else 0

                # Dashboard ROI multiplied by leverage - includes leverage tax effect
                dashboard_roi_percent = roi_percent * leverage

                # Margin ratio (Binance-style) - shows % buffer before liquidation
                if mark_price > 0 and liquidation_price > 0:
                    margin_ratio = ((mark_price - liquidation_price) / mark_price) * 100 if position_amt > 0 else ((liquidation_price - mark_price) / mark_price) * 100
                else: 
                    margin_ratio = 0

                # Dashboard margin ratio shows actual margin buffer (NOT multiplied by leverage)
                dashboard_margin_ratio = abs(margin_ratio)
                
                open_orders = get_open_orders_for_symbol(pos.get('symbol'), user_id)
                
                open_positions.append({
                    'symbol': pos.get('symbol'), 
                    'side': 'LONG' if position_amt > 0 else 'SHORT',
                    'amount': abs(position_amt), 
                    'size_usdt': abs(notional), 
                    'margin_usdt': initial_margin,
                    'margin_ratio': abs(margin_ratio),  # Raw
                    'dashboard_margin_ratio': dashboard_margin_ratio,  # Actual margin buffer %
                    'entry_price': entry_price, 
                    'mark_price': mark_price,
                    'unrealized_pnl': unrealized_pnl, 
                    'roi_percent': roi_percent,  # Raw
                    'dashboard_roi_percent': dashboard_roi_percent,  # Actual ROI %
                    'leverage': leverage,
                    'liquidation_price': liquidation_price, 
                    'open_orders': open_orders,
                    'timestamp': datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                })
        
        # Cache the results
        _positions_cache[cache_key] = open_positions
        _positions_cache_time[cache_key] = current_time
        return open_positions
    except Exception as e:
        print(f"Error getting open positions: {e}")
        return []

def get_open_positions_live(user_id=None):
    """
    ✅ LIVE VERSION - Fetches fresh position data WITHOUT caching
    Used specifically for real-time liquidation price updates
    This bypasses all caching to ensure the most current data
    """
    try:
        client = get_client(user_id)
        if client is None: 
            return []
        
        positions = client.futures_position_information(recvWindow=10000)
        open_positions = []
        
        for pos in positions:
            position_amt = float(pos.get('positionAmt', 0))
            if abs(position_amt) > 0:
                entry_price = float(pos.get('entryPrice', 0))
                mark_price = float(pos.get('markPrice', 0))
                unrealized_pnl = float(pos.get('unRealizedProfit', 0))
                liquidation_price = float(pos.get('liquidationPrice', 0))
                leverage = int(pos.get('leverage', 1))
                notional = float(pos.get('notional', 0))
                
                initial_margin = abs(notional) / leverage if leverage > 0 else abs(notional)
                # ROI percent (Binance-style, based on margin used)
                roi_percent = (unrealized_pnl / initial_margin * 100) if initial_margin > 0 else 0

                # Dashboard ROI multiplied by leverage - includes leverage tax effect
                dashboard_roi_percent = roi_percent * leverage

                # Margin ratio (Binance-style) - shows % buffer before liquidation
                if mark_price > 0 and liquidation_price > 0:
                    margin_ratio = ((mark_price - liquidation_price) / mark_price) * 100 if position_amt > 0 else ((liquidation_price - mark_price) / mark_price) * 100
                else: 
                    margin_ratio = 0

                # Dashboard margin ratio shows actual margin buffer (NOT multiplied by leverage)
                dashboard_margin_ratio = abs(margin_ratio)
                
                open_orders = get_open_orders_for_symbol(pos.get('symbol'), user_id)
                
                open_positions.append({
                    'symbol': pos.get('symbol'), 
                    'side': 'LONG' if position_amt > 0 else 'SHORT',
                    'amount': abs(position_amt), 
                    'size_usdt': abs(notional), 
                    'margin_usdt': initial_margin,
                    'margin_ratio': abs(margin_ratio),  # Raw
                    'dashboard_margin_ratio': dashboard_margin_ratio,  # Actual margin buffer %
                    'entry_price': entry_price, 
                    'mark_price': mark_price,
                    'unrealized_pnl': unrealized_pnl, 
                    'roi_percent': roi_percent,  # Raw
                    'dashboard_roi_percent': dashboard_roi_percent,  # Actual ROI %
                    'leverage': leverage,
                    'liquidation_price': liquidation_price, 
                    'open_orders': open_orders,
                    'timestamp': datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                })
        
        # NO CACHING - Return fresh data immediately
        return open_positions
    except Exception as e:
        print(f"Error getting live open positions: {e}")
        return []

def get_open_orders_for_symbol(symbol, user_id=None):
    try:
        client = get_client(user_id)
        if client is None: 
            return []
        
        orders = client.futures_get_open_orders(symbol=symbol, recvWindow=10000)
        return [{
            'orderId': o.get('orderId'), 
            'type': o.get('type'), 
            'side': o.get('side'),
            'price': float(o.get('stopPrice', o.get('price', 0))),
            'origQty': float(o.get('origQty', 0)), 
            'status': o.get('status')
        } for o in orders]
    except Exception:
        return []

def get_user_daily_stats(user_id):
    """Get or create today's stats from DB"""
    today = datetime.utcnow().date().isoformat()
    stat = TradeDailyStats.get_for_user(user_id, today)
    db.session.commit()
    return stat

def update_trade_stats(symbol, user_id):
    """Update trade stats in DB"""
    stat = get_user_daily_stats(user_id)
    stat.total_trades += 1
    symbols = stat.get_symbol_trades()
    symbols[symbol] = symbols.get(symbol, 0) + 1
    stat.set_symbol_trades(symbols)
    db.session.commit()


def can_open_trade(symbol, user_id):
    stat = get_user_daily_stats(user_id)
    if stat.total_trades >= config.MAX_TRADES_PER_DAY:
        return False, f"Daily limit of {config.MAX_TRADES_PER_DAY} trades reached ({stat.total_trades}/{config.MAX_TRADES_PER_DAY})"
    symbols = stat.get_symbol_trades()
    sym_count = symbols.get(symbol, 0)
    if sym_count >= config.MAX_TRADES_PER_SYMBOL_PER_DAY:
        return False, f"Daily limit of {config.MAX_TRADES_PER_SYMBOL_PER_DAY} trades for {symbol} reached ({sym_count}/{config.MAX_TRADES_PER_SYMBOL_PER_DAY})"
    return True, None

def get_today_stats(user_id):
    stat = get_user_daily_stats(user_id)
    symbols = stat.get_symbol_trades()
    return {
        "total_trades": stat.total_trades,
        "max_trades": config.MAX_TRADES_PER_DAY,
        "symbol_trades": symbols,
        "max_per_symbol": config.MAX_TRADES_PER_SYMBOL_PER_DAY
    }
def get_exchange_max_leverage(symbol, client=None):
    """
    Fetches the actual maximum available leverage for a symbol from Binance Futures.
    """
    try:
        if not client:
            # Fallback to your default client logic if a user client isn't passed
            client = _default_client 
            
        # Fetch leverage brackets for the specific symbol
        brackets_info = client.futures_leverage_bracket(symbol=symbol)
        
        # Binance returns a list. Bracket 1 (the first item) always contains the highest leverage
        if brackets_info and isinstance(brackets_info, list) and len(brackets_info) > 0:
            max_lev = brackets_info[0]['brackets'][0]['initialLeverage']
            return int(max_lev)
            
    except Exception as e:
        print(f"API failed to fetch max leverage for {symbol}: {e}")
        
    # FALLBACK: If API fails, default to your KNOWN_LEVERAGE_MAP
    # IMPORTANT: Change the default from a dangerous 125 to a safe 20!
    return KNOWN_LEVERAGE_MAP.get(symbol, 20)

def execute_trade_action(balance, symbol, side, entry, order_type, sl_type, sl_value, sizing, user_units, user_lev, margin_mode, tp1, tp1_pct, tp2, user_id=None):
    from models import TradePosition, db
    import config
    global _positions_cache_time
    client = get_client(user_id)
    if not client: 
        return {"success": False, "message": "❌ No Binance connection"}
    
    try:
        # STRICT MANDATORY SL CHECK - Must be > 0
        if not sl_value or sl_value <= 0:
            return {"success": False, "message": "🚫 SL MANDATORY - You must set a Stop Loss value (1% minimum required)"}

        if entry <= 0:
            return {"success": False, "message": "❌ Invalid entry price"}

        if order_type not in ["MARKET", "LIMIT"]:
            return {"success": False, "message": "❌ MARKET/LIMIT only"}

        if order_type == "LIMIT" and entry <= 0:
            return {"success": False, "message": "❌ LIMIT needs entry price"}

        # 1% STRICT ENFORCEMENT - NO OVERRIDES
        suggested_units = sizing.get("suggested_units", 0)
        suggested_leverage = sizing.get("suggested_leverage", 1)
        
        # Handle empty/None user inputs
        if not user_units or user_units <= 0:
            user_units = suggested_units
        if not user_lev or user_lev <= 0:
            user_lev = suggested_leverage
            
        if user_units > suggested_units:
            return {"success": False, "message": f"🚫 Qty > 1% risk max ({suggested_units:.6f} units)"}
        if user_lev > suggested_leverage:
            return {"success": False, "message": f"🚫 Lev > safe max ({suggested_leverage}x for 1% risk)"}

        qty = round_qty(symbol, user_units or suggested_units, user_id)
        if qty <= 0:
            return {"success": False, "message": "❌ Qty too small (below min notional)"}

        required_qty = get_required_order_qty(symbol, entry, user_id)
        if qty < required_qty:
            return {"success": False, "message": f"❌ Below Binance min ({required_qty:.6f} units), got {qty:.6f}"}

        lev = int(user_lev or suggested_leverage)
        
        # Limits check
        can_trade, limit_msg = can_open_trade(symbol, user_id)
        if not can_trade:
            return {"success": False, "message": limit_msg}

        # ✅ SMART LEVERAGE FALLBACK - Try ALL common leverage values
        original_lev = lev
        leverage_set = False
        
        # Build comprehensive list: start with requested, then try all common values
        # Common Binance leverage: 125, 100, 75, 50, 25, 20, 15, 10, 5, 3, 2, 1
        all_leverages = [125, 100, 75, 50, 25, 20, 15, 10, 5, 3, 2, 1]
        
        # Try requested leverage first
        leverage_attempts = [original_lev] + [x for x in all_leverages if x < original_lev and x != original_lev]
        leverage_attempts = list(dict.fromkeys(leverage_attempts))  # Remove duplicates, preserve order
        
        print(f"📊 Trying leverages for {symbol}: {leverage_attempts}")
        
        for attempt_lev in leverage_attempts:
            try:
                client.futures_change_leverage(symbol=symbol, leverage=attempt_lev)
                lev = attempt_lev
                leverage_set = True
                if attempt_lev < original_lev:
                    print(f"✅ Leverage adjusted: {original_lev}x → {attempt_lev}x")
                break
            except BinanceAPIException as e:
                if e.code == 4028:  # Leverage not valid for this coin/account
                    print(f"   ⚠️ {attempt_lev}x rejected for {symbol}")
                    continue
                else:
                    # Unexpected error
                    print(f"   ❌ Leverage error {e.code}: {e}")
                    raise
            except Exception as e:
                print(f"   ⚠️ Leverage {attempt_lev}x error: {e}")
                continue
        
        if not leverage_set:
            return {
                "success": False, 
                "message": f"❌ No valid leverage found for {symbol} on your account.\n"
                           f"Requested: {original_lev}x\n"
                           f"Tried: {', '.join(map(str, leverage_attempts))}\n"
                           f"All rejected. Your account/coin may have restrictions.\n"
                           f"Try: Contact Binance support or use a different coin."
            }
        
        # Try to set margin type (silently ignore if already set)
        try:
            client.futures_change_margin_type(symbol=symbol, marginType=margin_mode)
        except BinanceAPIException as e:
            # These are non-fatal - already set or not needed
            if e.code in [4046, 4048]:  # Already in this mode, or no need to change
                print(f"ℹ️ Margin mode already set to {margin_mode}")
                pass
            else:
                # Only fail for actual margin mode errors
                print(f"⚠️ Margin mode issue (non-fatal, continuing): {e}")
                pass
        except Exception as e:
            # Ignore margin mode errors - they don't block trades
            print(f"⚠️ Margin mode error (ignored): {str(e)}")
            pass

        e_side = Client.SIDE_BUY if side == "LONG" else Client.SIDE_SELL
        x_side = Client.SIDE_SELL if side == "LONG" else Client.SIDE_BUY
        
        # Calc SL price
        if sl_type == "SL % Movement":
            calculated_sl = entry * (1 - sl_value/100) if side == "LONG" else entry * (1 + sl_value/100)
        else:
            calculated_sl = sl_value
        sl_p = round_price(symbol, calculated_sl, user_id)

        # MARGIN REQUIREMENT CHECK
        notional_value = float(qty) * float(entry)
        margin_required = notional_value / float(lev)
        available_margin = float(balance) * 0.95  # Use 95% to add safety buffer
        
        if margin_required > available_margin:
            return {
                "success": False,
                "message": f"❌ Insufficient margin:\n"
                           f"   Need: ${margin_required:.2f}\n"
                           f"   Available: ${available_margin:.2f}\n"
                           f"   Shortfall: ${margin_required - available_margin:.2f}\n"
                           f"   Try: Lower quantity, reduce leverage, or increase stop loss %"
            }

        # MAIN ORDER - This MUST succeed
        try:
            order_params = {"symbol": symbol, "side": e_side, "type": order_type, "quantity": qty}
            if order_type == "LIMIT":
                order_params["price"] = round_price(symbol, entry, user_id)
                order_params["timeInForce"] = "GTC"
            client.futures_create_order(**order_params)
            time.sleep(0.5)
        except Exception as e:
            return {"success": False, "message": f"❌ Main order failed: {str(e)}"}

        # SL ORDER - Create with error tolerance
        try:
            client.futures_create_order(symbol=symbol, side=x_side, type="STOP_MARKET", 
                stopPrice=sl_p, closePosition=True, workingType="MARK_PRICE")
        except Exception as e:
            print(f"⚠️ SL order creation failed: {e}")
            log_trade_event("TRADE_WARN", f"⚠️ SL order failed but main position created: {str(e)}", user_id)

        # TP1 PARTIAL - Create with error tolerance  
        try:
            if tp1 > 0 and ((side=="LONG" and tp1>entry) or (side=="SHORT" and tp1<entry)):
                t1_qty = round_qty(symbol, qty * (tp1_pct/100), user_id)
                if t1_qty > 0:
                    client.futures_create_order(symbol=symbol, side=x_side, type="TAKE_PROFIT_MARKET",
                        stopPrice=round_price(symbol, tp1, user_id), quantity=t1_qty, reduceOnly=True, workingType="MARK_PRICE")
        except Exception as e:
            print(f"⚠️ TP1 order creation failed: {e}")
            log_trade_event("TRADE_WARN", f"⚠️ TP1 order failed: {str(e)}", user_id)

        # TP2 REMAINDER - Create with error tolerance
        try:
            if tp2 > 0 and ((side=="LONG" and tp2>entry) or (side=="SHORT" and tp2<entry)):
                client.futures_create_order(symbol=symbol, side=x_side, type="TAKE_PROFIT_MARKET",
                    stopPrice=round_price(symbol, tp2, user_id), closePosition=True, workingType="MARK_PRICE")
        except Exception as e:
            print(f"⚠️ TP2 order creation failed: {e}")
            log_trade_event("TRADE_WARN", f"⚠️ TP2 order failed: {str(e)}", user_id)

        # CREATE DB POSITION RECORD
        pos = TradePosition(
            user_id=user_id, symbol=symbol, side=side,
            entry_price=entry, initial_qty=qty, sl_price=calculated_sl,
            tp1_price=tp1, tp1_qty_pct=tp1_pct, tp2_price=tp2,
            current_sl=calculated_sl
        )
        db.session.add(pos)
        db.session.commit()

        # UPDATE STATS & LOG
        update_trade_stats(symbol, user_id)
        
        # Show if leverage was adjusted
        lev_note = f" (Adjusted from {original_lev}x)" if lev < original_lev else ""
        log_trade_event("TRADE_OPEN", f"✅ 1% RISK {side} {symbol} | Entry:${entry:.4f} SL:${sl_p:.4f} Qty:{qty} Lev:{lev}x{lev_note}", user_id)

        # Cache invalidation
        if f"positions_{user_id}" in _positions_cache: del _positions_cache[f"positions_{user_id}"]
        if f"trade_history_{user_id}" in _trade_history_cache: del _trade_history_cache[f"trade_history_{user_id}"]

        return {"success": True, "message": f"✅ {side} {symbol} executed (1% risk) @ {lev}x leverage{lev_note}. DB tracked."}
        
    except Exception as e:
        db.session.rollback()
        log_trade_event("TRADE_FAIL", f"❌ {str(e)}", user_id)
        return {"success": False, "message": f"❌ {str(e)}"}

def partial_close_position(symbol, close_percent=None, close_qty=None, user_id=None):
    try:
        client = get_client(user_id)
        if client is None: 
            return {"success": False, "message": "Connection Failed"}
        
        positions = client.futures_position_information(symbol=symbol)
        pos = next((p for p in positions if abs(float(p.get('positionAmt', 0))) > 0), None)
        
        if not pos: 
            return {"success": False, "message": "No position found"}
        
        amt = float(pos.get('positionAmt', 0))
        q = round_qty(symbol, close_qty if close_qty else abs(amt) * (close_percent / 100), user_id)
        if q <= 0:
            return {"success": False, "message": "❌ Partial close amount is too small for Binance minimum size."}
        side = Client.SIDE_SELL if amt > 0 else Client.SIDE_BUY
        
        order = client.futures_create_order(symbol=symbol, side=side, type="MARKET", quantity=q)
        log_trade_event("PARTIAL_CLOSE", f"Closed {q} units of {symbol}, PnL: ${order.get('realizedPnl', 0):.2f}", user_id)
        # Invalidate caches after partial close
        cache_key_positions = f"positions_{user_id or 'public'}"
        cache_key_trades = f"trade_history_{user_id or 'public'}"
        if cache_key_positions in _positions_cache:
            del _positions_cache[cache_key_positions]
        if cache_key_trades in _trade_history_cache:
            del _trade_history_cache[cache_key_trades]
        return {"success": True, "message": f"Closed {q} units", "order": order}
    
    except Exception as e:
        return {"success": False, "message": str(e)}

def close_position(symbol, user_id=None):
    try:
        client = get_client(user_id)
        positions = client.futures_position_information(symbol=symbol)
        pos = next((p for p in positions if abs(float(p.get('positionAmt', 0))) > 0), None)
        if not pos: return {"success": False, "message": "No position"}
        
        amt = abs(float(pos.get('positionAmt', 0)))
        side = Client.SIDE_SELL if float(pos.get('positionAmt', 0)) > 0 else Client.SIDE_BUY
        client.futures_create_order(symbol=symbol, side=side, type="MARKET", quantity=amt)
        client.futures_cancel_all_open_orders(symbol=symbol)
        log_trade_event("TRADE_CLOSE", f"Closed full position {symbol}", user_id)
        # Invalidate caches after position close
        cache_key_positions = f"positions_{user_id or 'public'}"
        cache_key_trades = f"trade_history_{user_id or 'public'}"
        if cache_key_positions in _positions_cache:
            del _positions_cache[cache_key_positions]
        if cache_key_trades in _trade_history_cache:
            del _trade_history_cache[cache_key_trades]
        return {"success": True, "message": "Position Closed"}
    except Exception as e: 
        return {"success": False, "message": str(e)}

def trail_stop_loss(symbol, user_id=None):
    """Dynamic trailing SL: positive only, max -1% loss from entry"""
    from models import TradePosition, db
    import config
    try:
        pos_db = TradePosition.query.filter_by(user_id=user_id, symbol=symbol, status='open').first()
        if not pos_db: 
            return {"success": False, "message": "No tracked open position"}

        client = get_client(user_id)
        if not client: 
            return {"success": False, "message": "No connection"}

        positions = client.futures_position_information(symbol=symbol)
        pos = next((p for p in positions if abs(float(p['positionAmt'] or 0)) > 0), None)
        if not pos:
            return {"success": False, "message": "No exchange position"}

        entry = pos_db.entry_price
        mark = float(pos['markPrice'] or 0)
        current_sl = pos_db.current_sl
        side = pos_db.side

        if mark <= 0: 
            return {"success": False, "message": "Invalid mark price"}

        # TRAIL: Positive moves only, cap total loss at -1% entry
        trail_pct = config.MAX_TRAIL_LOSS_PCT / 100.0
        if side == 'LONG':
            new_sl = max(current_sl, mark * (1 - trail_pct))
            loss_cap = entry * (1 - trail_pct)
            new_sl = min(new_sl, mark * 0.998)  # Tight buffer
        else:
            new_sl = min(current_sl, mark * (1 + trail_pct))
            loss_cap = entry * (1 + trail_pct)
            new_sl = max(new_sl, mark * 1.002)

        new_sl = round_price(symbol, new_sl, user_id)
        move_pct = abs((new_sl - current_sl) / entry * 100)

        if move_pct > 0.05:  # Min 0.05% move
            client.futures_create_order(
                symbol=symbol, side="BOTH", type="STOP_MARKET",
                stopPrice=new_sl, closePosition=True, workingType="MARK_PRICE"
            )
            pos_db.update_trail_sl(new_sl)
            db.session.commit()
            log_trade_event("TRAIL_SL", f"{symbol}: SL {current_sl:.4f}→{new_sl:.4f} ({move_pct:+.2f}%)", user_id)
            return {"success": True, "sl_new": new_sl, "move": f"{move_pct:+.2f}%"}
        return {"success": True, "message": "Trail optimal - no update"}
    except Exception as e:
        db.session.rollback()
        return {"success": False, "message": str(e)}

def update_stop_loss(symbol, new_sl_percent, user_id=None):
    """Legacy - call trail_stop_loss for dynamic trailing"""
    return trail_stop_loss(symbol, user_id)

def get_live_pnl(symbol, user_id=None):
    """Live PnL from DB position + exchange"""
    from models import TradePosition, db
    try:
        pos_db = TradePosition.query.filter_by(user_id=user_id, symbol=symbol).order_by(TradePosition.updated_at.desc()).first()
        if not pos_db:
            return {"success": False, "error": "No position record"}

        client = get_client(user_id)
        if client:
            positions = client.futures_position_information(symbol=symbol)
            pos = next((p for p in positions if abs(float(p.get('positionAmt', 0))) > 0), None)
            if pos:
                unrealized = float(pos.get('unRealizedProfit', 0))
                pos_db.unrealized_pnl = unrealized
                db.session.commit()
            else:
                unrealized = pos_db.unrealized_pnl or 0
        else:
            unrealized = pos_db.unrealized_pnl or 0

        roi_pct = (unrealized / (pos_db.initial_qty * pos_db.entry_price / pos_db.suggested_leverage)) * 100 if pos_db.initial_qty > 0 else 0
        return {
            "success": True, 
            "pnl": unrealized, 
            "roi_pct": round(roi_pct, 2), 
            "status": pos_db.status, 
            "sl_current": pos_db.current_sl
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

def get_trade_history(user_id=None):
    global _trade_history_cache, _trade_history_cache_time
    current_time = time.time()
    cache_key = f"trade_history_{user_id or 'public'}"
    
    # Return cached trade history if less than 60 seconds old
    if cache_key in _trade_history_cache and (current_time - _trade_history_cache_time.get(cache_key, 0)) < 60:
        return _trade_history_cache[cache_key]
    
    try:
        client = get_client(user_id)
        trades = client.futures_account_trades(limit=500)
        trade_history = [{
            'time': datetime.fromtimestamp(t.get('time', 0)/1000).strftime("%Y-%m-%d %H:%M:%S"), 
            'symbol': t.get('symbol'), 
            'side': 'LONG' if t.get('side') == 'BUY' else 'SHORT', 
            'qty': float(t.get('qty', 0)), 
            'price': float(t.get('price', 0)), 
            'realized_pnl': float(t.get('realizedPnl', 0)), 
            'commission': float(t.get('commission', 0)), 
            'order_id': t.get('orderId')
        } for t in sorted(trades, key=lambda x: x.get('time', 0), reverse=True)]
        
        # Cache the results
        _trade_history_cache[cache_key] = trade_history
        _trade_history_cache_time[cache_key] = current_time
        return trade_history
    except Exception: 
        return []

def get_user_trade_positions_with_tp_sl(user_id=None):
    """
    Fetch user's active and recent trade positions from database with TP/SL levels.
    Returns formatted data for dashboard display.
    """
    from models import TradePosition
    try:
        if not user_id:
            return []
        
        # Get all positions (open and recent closed)
        positions = TradePosition.query.filter_by(user_id=user_id).order_by(
            TradePosition.status.desc(),  # Open first
            TradePosition.updated_at.desc()  # Most recent
        ).limit(100).all()
        
        formatted_positions = []
        for pos in positions:
            formatted_positions.append({
                'id': pos.id,
                'symbol': pos.symbol,
                'side': pos.side,
                'entry_price': round(pos.entry_price, 2),
                'initial_qty': round(pos.initial_qty, 6),
                'sl_price': round(pos.sl_price, 2) if pos.sl_price else 0,
                'current_sl': round(pos.current_sl, 2) if pos.current_sl else 0,
                'tp1_price': round(pos.tp1_price, 2) if pos.tp1_price else 0,
                'tp1_qty_pct': pos.tp1_qty_pct,
                'tp2_price': round(pos.tp2_price, 2) if pos.tp2_price else 0,
                'unrealized_pnl': round(pos.unrealized_pnl, 2) if pos.unrealized_pnl else 0,
                'status': pos.status,
                'created_at': pos.created_at.strftime("%Y-%m-%d %H:%M:%S") if pos.created_at else "",
                'updated_at': pos.updated_at.strftime("%Y-%m-%d %H:%M:%S") if pos.updated_at else ""
            })
        
        return formatted_positions
    except Exception as e:
        print(f"Error fetching trade positions with TP/SL: {e}")
        return []

def log_trade_event(event_type, message, user_id=None, pnl=0.0):
    """Log a trade event for live monitoring - DB + Session"""
    if user_id:
        # DB Log
        log_entry = TradeLog(
            user_id=user_id,
            event_type=event_type,
            message=message,
            pnl=float(pnl)
        )
        db.session.add(log_entry)
        db.session.commit()
    
    # Session for live UI (fallback/hybrid)
    if "trade_events" not in session:
        session["trade_events"] = []
    
    event = {
        "timestamp": datetime.utcnow().strftime("%H:%M:%S"),
        "type": event_type,
        "message": message,
        "user_id": user_id,
        "pnl": pnl
    }
    
    session["trade_events"].insert(0, event)
    if len(session["trade_events"]) > 50:
        session["trade_events"] = session["trade_events"][:50]
    session.modified = True

def get_trade_events(user_id=None):
    """Get recent trade events - DB primary + session"""
    events = []
    if user_id:
        db_logs = TradeLog.get_recent(user_id, 30)
        events = [{
            "timestamp": log.timestamp.strftime("%H:%M:%S"),
            "type": log.event_type,
            "message": log.message,
            "user_id": user_id,
            "pnl": log.pnl
        } for log in db_logs]
    
    # Merge with session (latest first)
    session_events = session.get("trade_events", [])[:20]
    all_events = events[:10] + session_events[:10]  # Hybrid top 20
    return all_events