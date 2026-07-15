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
_virtual_guard_last_run = {}

# TTL cache + ban-aware short-circuit for conditional-orders endpoint.
# Prevents the duplicate UI pollers from hammering Binance and triggering -1003 IP bans.
_conditional_cache = {}          # {user_id: (ts_ms, [orders])}
_conditional_ban_until = 0       # ms epoch; while now < this, skip the call
CONDITIONAL_CACHE_MS = 3000

def invalidate_conditional_cache(user_id):
    """Call this after placing any TP/SL order to force fresh fetch on next poll."""
    global _conditional_cache
    if user_id in _conditional_cache:
        del _conditional_cache[user_id]
    print(f"[CACHE] Conditional cache invalidated for user {user_id}")

def _fetch_papi(client, path, params=None):
    """
    Make a signed request to the Binance Portfolio Margin (papi) base URL.
    Used as fallback when the account is a Portfolio Margin account.
    path example: '/papi/v1/um/openOrders'
    """
    import hmac
    import hashlib
    import urllib.parse
    import requests as _requests
    
    base_url = 'https://papi.binance.com'
    params = params or {}
    params['timestamp'] = int(time.time() * 1000)
    if hasattr(client, 'timestamp_offset') and client.timestamp_offset:
        params['timestamp'] += client.timestamp_offset
    params['recvWindow'] = params.get('recvWindow', 10000)
    
    query_string = urllib.parse.urlencode(params)
    signature = hmac.new(
        client.API_SECRET.encode('utf-8'),
        query_string.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    query_string += '&signature=' + signature
    
    url = base_url + path + '?' + query_string
    headers = {'X-MBX-APIKEY': client.API_KEY}
    
    proxies = {}
    if hasattr(config, 'PROXY_URL') and config.PROXY_URL:
        proxies = {'https': config.PROXY_URL, 'http': config.PROXY_URL}
    
    resp = _requests.get(url, headers=headers, proxies=proxies, timeout=15)
    resp.raise_for_status()
    return resp.json()

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

# logic.py — symbol selector (uses real existing functions, invalidates caches)
_trades_cache = {}
_trades_cache_time = {}
_analysis_cache = {}

def select_symbol(user_id, symbol):
    """
    Called when the user picks a new symbol in the UI.
    Invalidates per-user caches and synchronously fetches fresh
    positions, recent trades, and coin analysis so the frontend
    receives everything in ONE response (no second-click delay).
    """
    # 1. Invalidate the REAL position cache used by get_open_positions()
    cache_key_pos = f"positions_{user_id or 'public'}"
    _positions_cache.pop(cache_key_pos, None)
    _positions_cache_time.pop(cache_key_pos, None)
    # Invalidate trade-history + analysis caches for this user/symbol
    cache_key_hist = f"trade_history_{user_id or 'public'}"
    _trade_history_cache.pop(cache_key_hist, None)
    _trade_history_cache_time.pop(cache_key_hist, None)
    _analysis_cache.pop(symbol, None)
    # 2. Remember the user's currently selected symbol (best-effort)
    try:
        session['selected_symbol'] = symbol
    except Exception:
        pass  # session may not be available outside a request context
    # 3. Fetch everything fresh, synchronously
    try:
        positions = get_open_positions(user_id) or []
    except Exception as e:
        print(f"[select_symbol] positions error: {e}")
        positions = []
    try:
        all_trades = get_trade_history(user_id, force_refresh=True) or []
        trades = [t for t in all_trades if t.get('symbol') == symbol][:50]
    except Exception as e:
        print(f"[select_symbol] trades error: {e}")
        trades = []
    # Coin analysis — empty here; your existing /api/analysis route still
    # populates that box. If you have a real analysis function, plug it in.
    analysis = {}
    # 4. Re-populate caches with the fresh data
    _trades_cache[user_id] = {"symbol": symbol, "data": trades}
    _trades_cache_time[user_id] = time.time()
    _analysis_cache[symbol] = analysis
    return {
        "symbol": symbol,
        "positions": positions,
        "recent_trades": trades,
        "coin_analysis": analysis,
    }

# User-specific client storage: {user_id: (client, last_verified_ts)}
_user_clients = {}
# Last connection error per user, so UI endpoints can show the REAL reason
_last_client_error = {}
# How long to trust a cached client before re-verifying with Binance.
# The old code called futures_account() (weight 5) on EVERY request from every
# poller, which is what triggered -1003 IP bans and killed the connection.
CLIENT_REVERIFY_SECONDS = 120

_public_ip_cache = {'ip': None, 'ts': 0}

def get_last_client_error(user_id):
    """Return the most recent connection error message for a user (or None)."""
    return _last_client_error.get(user_id)

def get_server_public_ip():
    """Best-effort fetch of the public egress IP Binance sees (cached 1h).
    Goes through PROXY_URL when configured, because that is the IP that
    must be whitelisted on the Binance API key."""
    now = time.time()
    if _public_ip_cache['ip'] and (now - _public_ip_cache['ts']) < 3600:
        return _public_ip_cache['ip']
    proxies = {}
    if getattr(config, 'PROXY_URL', None):
        proxies = {'https': config.PROXY_URL, 'http': config.PROXY_URL}
    for url in ('https://api.ipify.org', 'https://checkip.amazonaws.com'):
        try:
            ip = requests.get(url, timeout=5, proxies=proxies).text.strip()
            if ip and len(ip) <= 45:
                _public_ip_cache['ip'] = ip
                _public_ip_cache['ts'] = now
                return ip
        except Exception:
            continue
    return None

def describe_binance_error(e):
    """Turn a BinanceAPIException into an actionable, human-readable message."""
    msg = str(e)
    code = getattr(e, 'code', None)
    status = getattr(e, 'status_code', None)
    lower = msg.lower()

    if status == 451 or 'restricted location' in lower or 'unavailable in your region' in lower:
        if getattr(config, 'PROXY_URL', None):
            ip = get_server_public_ip()
            ip_hint = f" Binance currently sees requests coming from IP {ip}." if ip else ""
            return ("Binance geo-block (HTTP 451) even though PROXY_URL is set — the proxy's "
                    "exit IP is itself in a restricted region, or the proxy is unreachable. "
                    "Your API keys are fine. Verify the proxy's exit country is allowed "
                    f"(e.g. Singapore) and that the proxy URL/credentials are correct.{ip_hint}")
        return ("Binance blocks requests from this server's region (geo-restriction, HTTP 451). "
                "Your API keys are fine. PROXY_URL is NOT currently loaded by the app — set it "
                "in the .env file (PROXY_URL=http://user:pass@host:port) and restart the app, "
                "or host the app in a non-restricted region. Note: Google Cloud IPs often "
                "geolocate to the US even for Singapore VMs, so a proxy is usually required on GCP.")
    if code == -2015:
        ip = get_server_public_ip()
        ip_hint = f" This server's IP is {ip} — add it to the API key's IP whitelist." if ip else ""
        return ("Binance error -2015: Invalid API key, IP not whitelisted, or missing permissions. "
                "Check: 1) key/secret copied correctly, 2) 'Enable Futures' is ticked, "
                f"3) the key's IP restriction allows this server.{ip_hint}")
    if code == -2014:
        return "Binance error -2014: API key format invalid. Re-copy the key without spaces."
    if code == -1022:
        return "Binance error -1022: Signature invalid. Re-copy the API secret without spaces."
    if code == -1021:
        return "Binance error -1021: Timestamp out of sync. Retried with server-time offset; refresh and try again."
    if code == -1003:
        return "Binance error -1003: Rate limit / temporary IP ban. Wait 1-2 minutes and retry."
    return f"Binance error {code}: {msg}"

def get_user_exchange_client(user_id, include_disconnected=False):
    """
    Get Binance client for a specific user based on their saved exchange keys.
    Returns a Client on success, {'error': msg} on failure, None if the user
    has no saved connection at all.

    Self-healing: the DB lookup does NOT filter on is_connected — a stale
    'Disconnected' flag can never permanently block a working key. Whenever
    verification succeeds, is_connected/last_verified are updated so the UI
    shows Connected again.
    """
    from models import ExchangeConnection, db
    import config
    # Serve from cache; only re-verify after CLIENT_REVERIFY_SECONDS
    now = time.time()
    if not include_disconnected and user_id in _user_clients:
        client, verified_at = _user_clients[user_id]
        if (now - verified_at) < CLIENT_REVERIFY_SECONDS:
            return client
        try:
            client.futures_account(recvWindow=10000)
            _user_clients[user_id] = (client, now)
            return client
        except Exception as e:
            print(f"[CLIENT] Cached client invalid for user {user_id}: {e}, recreating...")
            _user_clients.pop(user_id, None)

    # Get user's saved keys regardless of the is_connected flag (self-heal)
    connection = ExchangeConnection.query.filter_by(
        user_id=user_id,
        exchange_type='binance'
    ).first()

    if not connection or not connection.api_key or not connection.api_secret:
        _last_client_error[user_id] = None  # no connection saved — not an error
        return None

    try:
        # Sync timestamp BEFORE client creation
        time_offset = sync_time_with_binance()

        req_params = {'timeout': 20}

        # Proxy support for geo-restrictions
        if hasattr(config, 'PROXY_URL') and config.PROXY_URL:
            req_params['proxies'] = {
                'https': config.PROXY_URL,
                'http': config.PROXY_URL
            }
            print(f"🌐 Using proxy: {config.PROXY_URL}")

        # Stripped keys: trailing whitespace from copy-paste is the #1 cause
        # of -2014/-2015 errors
        client = Client(
            api_key=connection.api_key.strip(),
            api_secret=connection.api_secret.strip(),
            requests_params=req_params
        )

        # Apply timestamp offset (PERMANENT -1021 FIX)
        if abs(time_offset) > 100:
            client.timestamp_offset = time_offset
            print(f"✅ Applied user client offset: {time_offset}ms")

        # Verify the keys actually work
        client.futures_account(recvWindow=10000)

        print(f"✅ User {user_id} Binance client created successfully")
        # Self-heal the DB status so the UI shows Connected
        try:
            if not connection.is_connected:
                print(f"🔄 Self-healing connection status for user {user_id}: Disconnected → Connected")
            connection.is_connected = True
            connection.last_verified = datetime.utcnow()
            db.session.commit()
        except Exception as db_err:
            db.session.rollback()
            print(f"⚠️ Could not persist connection status: {db_err}")
        _user_clients[user_id] = (client, time.time())
        _last_client_error[user_id] = None
        return client

    except BinanceAPIException as e:
        error_msg = describe_binance_error(e)
        print(f"❌ BinanceAPIException for user {user_id}: code={e.code}: {error_msg}")
        _last_client_error[user_id] = error_msg

        # Only mark as disconnected for permanent key errors — NOT for
        # transient ones (rate limits, timeouts), which self-heal above
        if e.code in [-2015, -2014, -1022]:
            try:
                connection.is_connected = False
                db.session.commit()
            except Exception:
                db.session.rollback()
        return {"error": error_msg}

    except Exception as e:
        msg = str(e)
        if '451' in msg or 'restricted location' in msg.lower():
            error_msg = describe_binance_error(e)
        else:
            error_msg = f"Could not reach Binance: {msg}"
        print(f"❌ Unexpected error creating client for user {user_id}: {e}")
        _last_client_error[user_id] = error_msg
        # Transient (network/timeout) — do not flip is_connected
        return {"error": error_msg}

def set_user_client(user_id, client):
    """Manually set the client for a user (for testing)"""
    _user_clients[user_id] = (client, time.time())

def clear_user_client(user_id):
    """Clear cached client for a user (when they disconnect)"""
    _user_clients.pop(user_id, None)
    _last_client_error.pop(user_id, None)

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
        if user_client and not isinstance(user_client, dict):
            return user_client
        if isinstance(user_client, dict):
            # User HAS a connection but it errored: return None instead of the
            # error dict (callers expect a client object). The error is stored
            # in get_last_client_error(user_id). Never silently fall back to
            # the site-wide default account for a user with their own keys.
            return None

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
        # Serve cached symbols while fresh (avoids an exchangeInfo call per page load)
        if _symbol_cache and (now - _symbol_cache_time) < config.SYMBOL_CACHE_DURATION:
            return _symbol_cache

        client_res = get_client(user_id)
        if not client_res or isinstance(client_res, dict):
            # No client / connection errored — fetch symbols from the PUBLIC
            # endpoint instead so the symbol list still populates
            proxies = {}
            if hasattr(config, 'PROXY_URL') and config.PROXY_URL:
                proxies = {'https': config.PROXY_URL, 'http': config.PROXY_URL}
            resp = requests.get('https://fapi.binance.com/fapi/v1/exchangeInfo', timeout=10, proxies=proxies)
            resp.raise_for_status()
            info = resp.json()
        else:
            client = client_res
            info = client.futures_exchange_info()
        
        # ✅ FIXED: Validate symbols more strictly
        symbols = sorted([
            s['symbol'] for s in info.get('symbols', []) 
            if s['status'] == 'TRADING' 
            and s['quoteAsset'] == 'USDT'
            and s['contractType'] == 'PERPETUAL'
            and len(s['symbol']) <= 20  # Binance symbols are reasonably short
            and s['symbol'].endswith('USDT')  # Must end with USDT
        ])
        
        _symbol_cache = symbols
        _symbol_cache_time = now
        return symbols
    except Exception as e:
        print(f"Error fetching symbols: {e}")
        return _symbol_cache if _symbol_cache else ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]

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

def get_open_positions(user_id=None, force_refresh=False):
    global _positions_cache, _positions_cache_time
    if force_refresh:
        cache_key = f"positions_{user_id or 'public'}"
        _positions_cache.pop(cache_key, None)
        _positions_cache_time.pop(cache_key, None)
    current_time = time.time()
    cache_key = f"positions_{user_id or 'public'}"
    
    # Return cached positions if less than 5 seconds old (for LIVE liquidation price updates)
    # Reduced from 30s to 5s for real-time liquidation price data
    if cache_key in _positions_cache and (current_time - _positions_cache_time.get(cache_key, 0)) < 5:
        return _positions_cache[cache_key]
    
    try:
        # Virtual TP/SL fallback enforcement (for accounts/symbols rejecting algo orders)
        run_virtual_tp_sl_guard(user_id)
        client = get_client(user_id)
        if client is None: 
            return []
        
        positions_raw = client.futures_position_information(recvWindow=10000)
        
        # After fetching positions, if empty, try Portfolio Margin endpoint
        if not positions_raw:
            try:
                pm_positions = _fetch_papi(client, '/papi/v1/um/positionRisk', {'recvWindow': 10000})
                if pm_positions and isinstance(pm_positions, list):
                    positions_raw = pm_positions
                    print(f"[DEBUG] Portfolio Margin positions found: {len(positions_raw)}")
            except Exception as pm_pos_err:
                print(f"[DEBUG] Portfolio Margin positions endpoint failed (non-fatal): {pm_pos_err}")

        open_positions = []
        
        for pos in positions_raw:
            position_amt = float(pos.get('positionAmt', 0))
            if abs(position_amt) > 0:
                entry_price = float(pos.get('entryPrice', 0))
                mark_price = float(pos.get('markPrice', 0))
                unrealized_pnl = float(pos.get('unRealizedProfit', 0))
                liquidation_price = float(pos.get('liquidationPrice', 0))
                leverage = int(pos.get('leverage', 0))
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
        # Virtual TP/SL fallback enforcement (for accounts/symbols rejecting algo orders)
        run_virtual_tp_sl_guard(user_id)
        client = get_client(user_id)
        if client is None: 
            return []
        
        positions_raw = client.futures_position_information(recvWindow=10000)
        
        # After fetching positions, if empty, try Portfolio Margin endpoint
        if not positions_raw:
            try:
                pm_positions = _fetch_papi(client, '/papi/v1/um/positionRisk', {'recvWindow': 10000})
                if pm_positions and isinstance(pm_positions, list):
                    positions_raw = pm_positions
                    print(f"[DEBUG] Portfolio Margin positions found: {len(positions_raw)}")
            except Exception as pm_pos_err:
                print(f"[DEBUG] Portfolio Margin positions endpoint failed (non-fatal): {pm_pos_err}")

        open_positions = []
        
        for pos in positions_raw:
            position_amt = float(pos.get('positionAmt', 0))
            if abs(position_amt) > 0:
                entry_price = float(pos.get('entryPrice', 0))
                mark_price = float(pos.get('markPrice', 0))
                unrealized_pnl = float(pos.get('unRealizedProfit', 0))
                liquidation_price = float(pos.get('liquidationPrice', 0))
                leverage = int(pos.get('leverage', 0))
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

def cancel_open_order(symbol, order_id, user_id=None):
    try:
        client = get_client(user_id)
        if not client:
            return {"success": False, "message": "No exchange connection"}
        client.futures_cancel_order(symbol=symbol, orderId=order_id, recvWindow=10000)
        invalidate_conditional_cache(user_id)
        return {"success": True, "message": f"Order {order_id} cancelled"}
    except Exception as e:
        return {"success": False, "message": str(e)}

def get_all_open_conditional_orders(user_id=None):
    global _conditional_ban_until
    now_ms = int(time.time() * 1000)
    print(f"[DEBUG] get_all_open_conditional_orders called. now_ms={now_ms}, ban_until={_conditional_ban_until}, cached={_conditional_cache.get(user_id)}")

    # Serve from short TTL cache when available — kills duplicate-poll storms.
    cached = _conditional_cache.get(user_id)
    if cached and (now_ms - cached[0]) < CONDITIONAL_CACHE_MS:
        # If cache is very fresh (under 1s), return even if empty
        if (now_ms - cached[0]) < 1000 or len(cached[1]) > 0:
            return list(cached[1])

    # If Binance has banned us, don't issue more requests until the window passes.
    if now_ms < _conditional_ban_until:
        if cached and len(cached[1]) > 0:
            return list(cached[1])
        return []

    try:
        client = get_client(user_id)
        if client is None:
            return []

        # --- Fetch regular open orders ---
        all_orders = []
        try:
            # FIX: Ensure we fetch all open orders including conditional ones
            all_orders = client.futures_get_open_orders(recvWindow=10000)
            
            if not all_orders:
                # Try Portfolio Margin (papi) endpoint for accounts using PM mode
                try:
                    pm_orders = _fetch_papi(client, '/papi/v1/um/openOrders', {'recvWindow': 10000})
                    if pm_orders and isinstance(pm_orders, list):
                        all_orders = pm_orders
                        print(f"[DEBUG] Portfolio Margin papi orders found: {len(all_orders)}")
                    elif pm_orders and isinstance(pm_orders, dict):
                        all_orders = pm_orders.get('orders', [])
                        print(f"[DEBUG] Portfolio Margin papi orders (dict): {len(all_orders)}")
                except Exception as pm_err:
                    print(f"[DEBUG] Portfolio Margin papi endpoint failed (non-fatal): {pm_err}")
                    # Final fallback: try old pmOpenOrders path
                    try:
                        pm_orders2 = client._request_futures_api('get', 'pmOpenOrders', True, data={'recvWindow': 10000})
                        if pm_orders2 and isinstance(pm_orders2, list):
                            all_orders = pm_orders2
                            print(f"[DEBUG] pmOpenOrders fallback orders found: {len(all_orders)}")
                        elif pm_orders2 and isinstance(pm_orders2, dict):
                            all_orders = pm_orders2.get('orders', [])
                    except Exception as pm_err2:
                        print(f"[DEBUG] pmOpenOrders fallback also failed: {pm_err2}")

            print(f"[DEBUG] ALL raw orders from Binance (unfiltered): {all_orders}")
            print(f"[DEBUG] Regular open orders count: {len(all_orders)}")
        except Exception as e:
            msg = str(e)
            print(f"[DEBUG] Error fetching regular open orders: {e}")
            # Honour Binance IP-ban so we stop hammering and extending the ban.
            if "-1003" in msg and "banned until" in msg:
                try:
                    _conditional_ban_until = int(msg.split("banned until")[1].split(".")[0].strip())
                except Exception:
                    _conditional_ban_until = now_ms + 60_000
                return list(cached[1]) if cached else []


        conditional_types = [
            'STOP', 'STOP_MARKET',
            'TAKE_PROFIT', 'TAKE_PROFIT_MARKET',
            'TRAILING_STOP_MARKET',
            'STOP_LOSS', 'STOP_LOSS_LIMIT',
            'TAKE_PROFIT_LIMIT',
            'LIMIT', 'LIMIT_MAKER',
            'TRAILING_STOP_MARKET_ALGO'
        ]

        conditional_orders = []
        seen_ids = set()

        for o in all_orders:
            o_type = o.get('type', '').upper()
            has_stop_price = float(o.get('stopPrice', 0)) > 0
            has_activate_price = float(o.get('activatePrice', 0)) > 0
            has_close_position = o.get('closePosition', False) == True

            if o_type in conditional_types or has_stop_price or has_activate_price or has_close_position:
                # Better labeling for TP1 vs SL
                label = 'SL'
                if 'TAKE_PROFIT' in o_type:
                    label = 'TP1'
                elif 'TRAILING' in o_type:
                    label = 'Trail SL'
                elif 'STOP' in o_type or 'STOP_LOSS' in o_type:
                    label = 'SL'
                elif o_type in ['LIMIT', 'LIMIT_MAKER'] and o.get('reduceOnly'):
                    label = 'TP2'
                
                oid = str(o.get('orderId') or '')
                if oid and oid not in seen_ids:
                    seen_ids.add(oid)
                    qty = float(o.get('origQty', 0))
                    conditional_orders.append({
                        'orderId': oid,
                        'symbol': o.get('symbol'),
                        'type': o_type,
                        'label': label,
                        'side': o.get('side'),
                        'stopPrice': float(o.get('stopPrice', 0)),
                        'price': float(o.get('price', 0)),
                        'origQty': qty,
                        'time': datetime.fromtimestamp(o.get('time', 0) / 1000).strftime('%Y-%m-%d %H:%M:%S') if o.get('time') else 'N/A',
                        'reduceOnly': o.get('reduceOnly', False),
                        'source': 'regular'
                    })

        # --- Fetch algo/conditional orders (TP1 lives here) ---
        try:
            algo_orders = []

            # Method 1: standard python-binance method
            if hasattr(client, 'futures_get_algo_orders'):
                try:
                    resp = client.futures_get_algo_orders(recvWindow=10000)
                    if resp:
                        algo_orders = resp if isinstance(resp, list) else resp.get('orders', [])
                        print(f"[DEBUG] Algo orders via futures_get_algo_orders: {len(algo_orders)}")
                except Exception as e1:
                    print(f"[DEBUG] futures_get_algo_orders failed: {e1}")

            # Method 2: fallback to raw request if Method 1 failed or returned nothing
            if not algo_orders and hasattr(client, '_request_futures_api'):
                try:
                    resp = client._request_futures_api('get', 'algoOrder/openOrders', True, data={'recvWindow': 10000})
                    if resp:
                        algo_orders = resp if isinstance(resp, list) else resp.get('orders', [])
                        print(f"[DEBUG] Algo orders via _request_futures_api: {len(algo_orders)}")
                except Exception as e2:
                    print(f"[DEBUG] _request_futures_api algo failed: {e2}")

            for o in algo_orders:
                o_type = (o.get('type') or o.get('algoType') or '').upper()
                algo_id = str(o.get('algoId') or o.get('orderId') or '')
                trigger_price = float(o.get('triggerPrice') or o.get('stopPrice') or 0)
                qty = float(o.get('qty') or o.get('origQty') or 0)
                book_time = o.get('bookTime') or o.get('time') or 0

                label = 'SL'
                if 'TAKE_PROFIT' in o_type:
                    label = 'TP1'
                elif 'TRAILING' in o_type:
                    label = 'Trail SL'
                elif 'STOP' in o_type or 'STOP_LOSS' in o_type:
                    label = 'SL'

                if algo_id not in seen_ids:
                    seen_ids.add(algo_id)
                    conditional_orders.append({
                        'orderId': algo_id,
                        'symbol': o.get('symbol'),
                        'type': o_type,
                        'label': label,
                        'side': o.get('side'),
                        'stopPrice': trigger_price,
                        'price': float(o.get('price') or 0),
                        'origQty': qty,
                        'time': datetime.fromtimestamp(int(book_time) / 1000).strftime('%Y-%m-%d %H:%M:%S') if book_time else 'N/A',
                        'reduceOnly': o.get('reduceOnly', True),
                        'source': 'algo'
                    })
        except Exception as algo_err:
            print(f"[DEBUG] Algo orders fetch failed (non-fatal): {algo_err}")

        # Sort by time descending
        conditional_orders.sort(key=lambda x: x['time'], reverse=True)
        
        # FIX: Reset virtual_guard_active flag if real orders exist for a symbol
        try:
            from models import TradePosition, db
            if conditional_orders:
                symbols_with_orders = set(o['symbol'] for o in conditional_orders)
                for sym in symbols_with_orders:
                    pos = TradePosition.query.filter_by(user_id=user_id, symbol=sym, status='open').first()
                    if pos and pos.virtual_guard_active:
                        print(f"[FIX] Resetting virtual_guard_active for {sym} as real orders found")
                        pos.virtual_guard_active = False
                        db.session.commit()
        except Exception as e:
            print(f"[DEBUG] Error resetting virtual guard flag: {e}")

        print(f"[DEBUG] Final conditional_orders to return: {conditional_orders}")
        _conditional_cache[user_id] = (now_ms, list(conditional_orders))
        return conditional_orders

    except Exception as e:
        print(f"Error fetching conditional orders: {e}")
        return []

def cancel_order(symbol, order_id, user_id=None):
    try:
        client = get_client(user_id)
        if client is None:
            return False, "Exchange connection not found"

        # Try regular cancel first
        try:
            client.futures_cancel_order(symbol=symbol, orderId=order_id)
            invalidate_conditional_cache(user_id)
            return True, "Order cancelled successfully"
        except BinanceAPIException as e:
            # If regular cancel fails, try algo cancel
            if e.code in [-2011, -2013]:  # Order does not exist as regular order
                try:
                    if hasattr(client, 'futures_cancel_algo_order'):
                        client.futures_cancel_algo_order(algoId=order_id)
                    elif hasattr(client, '_request_futures_api'):
                        client._request_futures_api('delete', 'algoOrder', True, data={'algoId': order_id})
                    invalidate_conditional_cache(user_id)
                    return True, "Algo order cancelled successfully"
                except Exception as algo_cancel_err:
                    return False, f"Algo cancel failed: {str(algo_cancel_err)}"
            return False, str(e)
    except Exception as e:
        print(f"Error cancelling order {order_id}: {e}")
        return False, str(e)

def run_virtual_tp_sl_guard(user_id=None):
    """
    Fallback TP/SL enforcement for symbols/accounts where Binance rejects algo order endpoints (-4120).
    Runs opportunistically during open-position fetches and closes/partials by market when levels are hit.
    """
    from models import TradePosition, db
    import config
    global _virtual_guard_last_run
    
    if not user_id: return
    now = time.time()
    interval = getattr(config, 'VIRTUAL_GUARD_INTERVAL_SECONDS', 1.0)
    if now - _virtual_guard_last_run.get(user_id, 0) < interval: return
    _virtual_guard_last_run[user_id] = now
    
    try:
        open_pos_db = TradePosition.query.filter_by(user_id=user_id, status='open').all()
        if not open_pos_db: return
        
        client = get_client(user_id)
        if not client: return
        
        for t in open_pos_db:
            sym = t.symbol
            mark = get_live_price(sym, user_id)
            if mark <= 0: continue
            
            side = t.side
            entry = t.entry_price
            
            def _is_sl_hit(sl_p):
                return (side == "LONG" and mark <= sl_p) or (side == "SHORT" and mark >= sl_p)
            def _is_tp_hit(tp_p):
                return (side == "LONG" and mark >= tp_p) or (side == "SHORT" and mark <= tp_p)
            
            # SL hit
            sl_p = float(t.current_sl or t.sl_price or 0)
            if sl_p > 0 and _is_sl_hit(sl_p):
                close_position(sym, user_id)
                log_trade_event("TRADE_WARN", f"🛡️ Virtual SL executed for {sym} @ {mark:.6f}", user_id)
                continue
            # TP1 partial (only once)
            tp1 = float(t.tp1_price or 0)
            tp1_pct = float(t.tp1_qty_pct or 0)
            remain = float(t.remain_qty_pct or 100.0)
            tp1_not_done = tp1 > 0 and tp1_pct > 0 and remain > (100.0 - tp1_pct + 0.1)
            if tp1_not_done and _is_tp_hit(tp1):
                partial_close_position(sym, close_percent=tp1_pct, user_id=user_id)
                log_trade_event("TRADE_WARN", f"🎯 Virtual TP1 executed for {sym} @ {mark:.6f}", user_id)
                continue
            # TP2 final close
            tp2 = float(t.tp2_price or 0)
            if tp2 > 0 and _is_tp_hit(tp2):
                close_position(sym, user_id)
                log_trade_event("🎯 Virtual TP2 executed for {sym} @ {mark:.6f}", user_id)
                continue
        db.session.commit()
    except Exception as e:
        try:
            db.session.rollback()
        except Exception:
            pass
        print(f"Virtual TP/SL guard error: {e}")

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
        # If TP1 price is set but TP1% left empty/zero, auto-allocate.
        # This avoids silent TP1 skips due to zero quantity.
        if tp1 and tp1 > 0 and (not tp1_pct or tp1_pct <= 0):
            tp1_pct = 50.0 if (tp2 and tp2 > 0) else 100.0
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
        main_order_id = ""
        try:
            order_params = {"symbol": symbol, "side": e_side, "type": order_type, "quantity": qty}
            if order_type == "LIMIT":
                order_params["price"] = round_price(symbol, entry, user_id)
                order_params["timeInForce"] = "GTC"
            main_resp = client.futures_create_order(**order_params)
            if isinstance(main_resp, dict) and main_resp.get("orderId") is not None:
                main_order_id = str(main_resp.get("orderId"))
            time.sleep(0.5)
        except Exception as e:
            return {"success": False, "message": f"❌ Main order failed: {str(e)}"}
        def _create_order_with_fallbacks(variants):
            errs = []
            for params in variants:
                try:
                    resp = client.futures_create_order(**params)
                    if isinstance(resp, dict) and resp.get("orderId"):
                        return True, resp, None
                    return True, resp, None
                except Exception as ex:
                    errs.append(str(ex))
                    continue
            return False, None, " | ".join(errs) if errs else "Unknown order placement error"
        def _submit_algo_order(params):
            """
            Submit Binance native conditional order via /fapi/v1/algoOrder.
            Supports multiple client versions.
            """
            # Newer python-binance variants
            if hasattr(client, "futures_create_algo_order"):
                return client.futures_create_algo_order(**params)
            if hasattr(client, "futures_v1_post_algo_order"):
                return client.futures_v1_post_algo_order(**params)
            # Fallback to low-level request for older clients
            if hasattr(client, "_request_futures_api"):
                return client._request_futures_api("post", "algoOrder", True, data=params)
            raise Exception("Algo order endpoint method not available in Binance client")
        def _create_algo_order_with_fallbacks(variants):
            errs = []
            for params in variants:
                try:
                    resp = _submit_algo_order(params)
                    if isinstance(resp, dict):
                        oid = resp.get("algoId") or resp.get("orderId")
                        if oid is not None:
                            return True, resp, None
                    return True, resp, None
                except Exception as ex:
                    errs.append(str(ex))
                    continue
            return False, None, " | ".join(errs) if errs else "Unknown algo-order placement error"
        def _short_error(err_text):
            e = str(err_text or "").strip()
            if not e:
                return "Unknown error"
            lo = e.lower()
            if "-4120" in lo or "algo order api endpoints" in lo:
                return "Algo order endpoint not supported for this symbol/account (-4120)"
            if "-1102" in lo and "quantity" in lo:
                return "Missing quantity parameter (-1102)"
            if len(e) > 140:
                e = e[:140] + "..."
            return e
        def _emergency_close_position():
            try:
                pinfo = client.futures_position_information(symbol=symbol)
                live_pos = next((p for p in pinfo if abs(float(p.get("positionAmt", 0))) > 0), None)
                if not live_pos:
                    return
                pos_amt = float(live_pos.get("positionAmt", 0))
                close_qty = round_qty(symbol, abs(pos_amt), user_id)
                if close_qty <= 0:
                    return
                close_side = Client.SIDE_SELL if pos_amt > 0 else Client.SIDE_BUY
                client.futures_create_order(symbol=symbol, side=close_side, type="MARKET", quantity=close_qty, reduceOnly=True)
                log_trade_event("TRADE_WARN", f"⚠️ Emergency close executed for {symbol} after SL placement failure", user_id)
            except Exception as close_err:
                log_trade_event("TRADE_WARN", f"⚠️ Emergency close failed for {symbol}: {close_err}", user_id)
        # SL ORDER - Create with error tolerance
        sl_created = False
        sl_error = None
        try:
            sl_variants = [
                {
                    "symbol": symbol,
                    "side": x_side,
                    "type": "STOP_MARKET",
                    "stopPrice": sl_p,
                    "closePosition": True,
                    "workingType": "MARK_PRICE",
                },
                {
                    "symbol": symbol,
                    "side": x_side,
                    "type": "STOP_MARKET",
                    "stopPrice": sl_p,
                    "closePosition": True,
                },
                {
                    "symbol": symbol,
                    "side": x_side,
                    "type": "STOP_MARKET",
                    "stopPrice": sl_p,
                    "quantity": qty,
                    "reduceOnly": True,
                    "workingType": "MARK_PRICE",
                },
                {
                    "symbol": symbol,
                    "side": x_side,
                    "type": "STOP_MARKET",
                    "stopPrice": sl_p,
                    "quantity": qty,
                    "reduceOnly": True,
                },
                {
                    "symbol": symbol,
                    "side": x_side,
                    "type": "STOP",
                    "stopPrice": sl_p,
                    "price": sl_p,
                    "quantity": qty,
                    "reduceOnly": True,
                    "timeInForce": "GTC",
                    "workingType": "MARK_PRICE",
                },
                {
                    "symbol": symbol,
                    "side": x_side,
                    "type": "STOP",
                    "stopPrice": sl_p,
                    "price": sl_p,
                    "quantity": qty,
                    "reduceOnly": True,
                    "timeInForce": "GTC",
                },
            ]
            sl_created, sl_order, sl_error = _create_order_with_fallbacks(sl_variants)
            if sl_created and sl_order and sl_order.get("orderId"):
                sl_created = True
                print(f"✅ SL order created: {sl_order['orderId']}")
            # Binance native algo endpoint fallback for -4120 style accounts
            if not sl_created:
                sl_algo_variants = [
                    {
                        "symbol": symbol,
                        "algoType": "CONDITIONAL",
                        "side": x_side,
                        "type": "STOP_MARKET",
                        "triggerPrice": sl_p,
                        "closePosition": "true",
                        "workingType": "MARK_PRICE",
                    },
                    {
                        "symbol": symbol,
                        "algoType": "CONDITIONAL",
                        "side": x_side,
                        "type": "STOP_MARKET",
                        "triggerPrice": sl_p,
                        "quantity": qty,
                        "reduceOnly": "true",
                        "workingType": "MARK_PRICE",
                    },
                    {
                        "symbol": symbol,
                        "algoType": "CONDITIONAL",
                        "side": x_side,
                        "type": "STOP",
                        "triggerPrice": sl_p,
                        "price": sl_p,
                        "quantity": qty,
                        "reduceOnly": "true",
                        "timeInForce": "GTC",
                        "workingType": "MARK_PRICE",
                    },
                ]
                sl_created, sl_algo_order, sl_algo_error = _create_algo_order_with_fallbacks(sl_algo_variants)
                if sl_created:
                    sl_order = sl_algo_order
                    sl_error = None
                    print(f"✅ SL algo order created: {sl_order}")
                else:
                    sl_error = sl_error or sl_algo_error
            time.sleep(0.3)
        except Exception as e:
            sl_error = str(e)
            print(f"⚠️ SL order creation failed: {e}")
            log_trade_event("TRADE_WARN", f"⚠️ SL order failed: {sl_error}", user_id)
        # HARD SAFETY:
        # If Binance rejects algo order type (-4120), switch to virtual guard fallback.
        # Otherwise, emergency-close to avoid unprotected exposure.
        virtual_guard_enabled = False
        sl_err_txt = (sl_error or "").lower()
        if (not sl_created) and ("-4120" in sl_err_txt or "algo order api endpoints" in sl_err_txt):
            virtual_guard_enabled = True
            log_trade_event("TRADE_WARN", f"⚠️ {symbol}: Exchange SL/TP algo not supported, virtual guard enabled", user_id)
        elif not sl_created:
            _emergency_close_position()
            return {
                "success": False,
                "message": f"❌ Trade aborted: Stop Loss order rejected by Binance.\n{sl_error or ''}\nPosition auto-closed for safety."
            }
        # TP1 PARTIAL - Create with error tolerance  
        tp1_created = False
        tp1_error = None
        tp1_qty = 0
        try:
            if tp1 > 0 and ((side=="LONG" and tp1>entry) or (side=="SHORT" and tp1<entry)):
                tp1_qty = round_qty(symbol, qty * (tp1_pct/100), user_id)
                if tp1_qty > 0:
                    tp1_price = round_price(symbol, tp1, user_id)
                    tp1_variants = [
                        {
                            "symbol": symbol,
                            "side": x_side,
                            "type": "TAKE_PROFIT_MARKET",
                            "stopPrice": tp1_price,
                            "quantity": tp1_qty,
                            "reduceOnly": True,
                            "workingType": "MARK_PRICE",
                        },
                        {
                            "symbol": symbol,
                            "side": x_side,
                            "type": "TAKE_PROFIT_MARKET",
                            "stopPrice": tp1_price,
                            "quantity": tp1_qty,
                            "reduceOnly": True,
                        },
                        {
                            "symbol": symbol,
                            "side": x_side,
                            "type": "TAKE_PROFIT",
                            "stopPrice": tp1_price,
                            "price": tp1_price,
                            "quantity": tp1_qty,
                            "reduceOnly": True,
                            "timeInForce": "GTC",
                            "workingType": "MARK_PRICE",
                        },
                        {
                            "symbol": symbol,
                            "side": x_side,
                            "type": "TAKE_PROFIT",
                            "stopPrice": tp1_price,
                            "price": tp1_price,
                            "quantity": tp1_qty,
                            "reduceOnly": True,
                            "timeInForce": "GTC",
                        },
                    ]
                    tp1_created, tp1_order, tp1_error = _create_order_with_fallbacks(tp1_variants)
                    if tp1_created and tp1_order and tp1_order.get("orderId"):
                        tp1_created = True
                        print(f"✅ TP1 order created: {tp1_order['orderId']}")
                    if not tp1_created:
                        tp1_algo_variants = [
                            {
                                "symbol": symbol,
                                "algoType": "CONDITIONAL",
                                "side": x_side,
                                "type": "TAKE_PROFIT_MARKET",
                                "triggerPrice": tp1_price,
                                "quantity": tp1_qty,
                                "reduceOnly": "true",
                                "workingType": "MARK_PRICE",
                            },
                            {
                                "symbol": symbol,
                                "algoType": "CONDITIONAL",
                                "side": x_side,
                                "type": "TAKE_PROFIT",
                                "triggerPrice": tp1_price,
                                "price": tp1_price,
                                "quantity": tp1_qty,
                                "reduceOnly": "true",
                                "timeInForce": "GTC",
                                "workingType": "MARK_PRICE",
                            },
                        ]
                        tp1_created, tp1_algo_order, tp1_algo_error = _create_algo_order_with_fallbacks(tp1_algo_variants)
                        if tp1_created:
                            tp1_order = tp1_algo_order
                            tp1_error = None
                            print(f"✅ TP1 algo order created: {tp1_order}")
                        else:
                            tp1_error = tp1_error or tp1_algo_error
            time.sleep(0.3)
        except Exception as e:
            tp1_error = str(e)
            print(f"⚠️ TP1 order creation failed: {e}")
            log_trade_event("TRADE_WARN", f"⚠️ TP1 order failed: {tp1_error}", user_id)
        # TP2 REMAINDER - Create with error tolerance
        tp2_created = False
        tp2_error = None
        try:
            if tp2 > 0 and ((side=="LONG" and tp2>entry) or (side=="SHORT" and tp2<entry)):
                # TP2 is the remaining quantity (Total Qty - TP1 Qty)
                # If TP1 failed or was not set, TP2 uses the full quantity
                actual_tp1_qty = tp1_qty if tp1_created else 0
                tp2_qty = round_qty(symbol, qty - actual_tp1_qty, user_id)

                if tp2_qty > 0:
                    tp2_price = round_price(symbol, tp2, user_id)
                    # TP2 is a Basic order (LIMIT or TAKE_PROFIT_MARKET with explicit quantity)
                    tp2_variants = [
                        {
                            "symbol": symbol,
                            "side": x_side,
                            "type": "LIMIT",
                            "price": tp2_price,
                            "quantity": tp2_qty,
                            "timeInForce": "GTC",
                            "reduceOnly": True,
                        },
                        {
                            "symbol": symbol,
                            "side": x_side,
                            "type": "TAKE_PROFIT_MARKET",
                            "stopPrice": tp2_price,
                            "quantity": tp2_qty,
                            "reduceOnly": True,
                            "workingType": "MARK_PRICE",
                        },
                    ]
                    tp2_created, tp2_order, tp2_error = _create_order_with_fallbacks(tp2_variants)
                    if tp2_created and tp2_order and tp2_order.get("orderId"):
                        tp2_created = True
                        print(f"✅ TP2 order created: {tp2_order['orderId']}")
                    
                    if not tp2_created:
                        # Fallback to algo if regular fails
                        tp2_algo_variants = [
                            {
                                "symbol": symbol,
                                "algoType": "CONDITIONAL",
                                "side": x_side,
                                "type": "TAKE_PROFIT_MARKET",
                                "triggerPrice": tp2_price,
                                "quantity": tp2_qty,
                                "reduceOnly": "true",
                                "workingType": "MARK_PRICE",
                            }
                        ]
                        tp2_created, tp2_algo_order, tp2_algo_error = _create_algo_order_with_fallbacks(tp2_algo_variants)
                        if tp2_created:
                            tp2_order = tp2_algo_order
                            tp2_error = None
                            print(f"✅ TP2 algo order created: {tp2_order}")
                        else:
                            tp2_error = tp2_error or tp2_algo_error
        except Exception as e:
            tp2_error = str(e)
            print(f"⚠️ TP2 order creation failed: {e}")
            log_trade_event("TRADE_WARN", f"⚠️ TP2 order failed: {tp2_error}", user_id)
        # PERSIST TO DATABASE
        pos = TradePosition(
            user_id=user_id,
            symbol=symbol,
            side=side,
            entry_price=entry,
            initial_qty=qty,
            remain_qty_pct=100.0,
            sl_price=sl_p,
            current_sl=sl_p,
            tp1_price=tp1 if tp1_created else None,
            tp1_qty_pct=tp1_pct if tp1_created else 0,
            tp2_price=tp2 if tp2_created else None,
            opening_order_id=main_order_id,
            status='open',
            virtual_guard_active=virtual_guard_enabled
        )
        db.session.add(pos)
        update_trade_stats(symbol, user_id)
        db.session.commit()
        # Build human-readable status message
        status_lines = [f"Main: ✅ ({main_order_id})"]
        status_lines.append(f"SL: {'✅' if sl_created else '❌'}")
        if tp1 > 0: status_lines.append(f"TP1: {'✅' if tp1_created else '❌'}")
        if tp2 > 0: status_lines.append(f"TP2: {'✅' if tp2_created else '❌'}")
        status_msg = " | ".join(status_lines)
        
        # Build warnings for failed orders
        warning_lines = []
        if not sl_created: warning_lines.append(f"SL: {_short_error(sl_error)}")
        if tp1 > 0 and not tp1_created: warning_lines.append(f"TP1: {_short_error(tp1_error)}")
        if tp2 > 0 and not tp2_created: warning_lines.append(f"TP2: {_short_error(tp2_error)}")
        
        lev_note = f" (adjusted from {original_lev}x)" if lev < original_lev else ""
        
        # De-duplicate repeated fallback errors (same root issue from multiple attempts)
        warning_msg = "\n".join(dict.fromkeys(warning_lines))
        
        # Build detailed log entry
        tp_parts = []
        if tp1 and tp1 > 0:
            tp_parts.append(f"TP1:${tp1:.4f}" + (f" [{tp1_pct:g}%]" if tp1_pct else ""))
        if tp2 and tp2 > 0:
            tp_parts.append(f"TP2:${tp2:.4f}")
        tp_note = (" | " + " ".join(tp_parts)) if tp_parts else ""
        log_trade_event(
            "TRADE_OPEN",
            f"✅ 1% RISK {side} {symbol} | Entry:${entry:.4f} SL:${sl_p:.4f}{tp_note} Qty:{qty} Lev:{lev}x{lev_note} | {status_msg}",
            user_id
        )
        # Cache invalidation
        cache_key_pos = f"positions_{user_id or 'public'}"
        _positions_cache.pop(cache_key_pos, None)
        _positions_cache_time.pop(cache_key_pos, None)
        cache_key_hist = f"trade_history_{user_id or 'public'}"
        _trade_history_cache.pop(cache_key_hist, None)
        _trade_history_cache_time.pop(cache_key_hist, None)
        # Fix 10: Invalidate conditional cache after trade execution
        invalidate_conditional_cache(user_id)
        
        # Final response with order status
        main_message = f"✅ {side} {symbol} executed (1% risk) @ {lev}x leverage{lev_note}"
        
        if warning_msg:
            final_message = (
                main_message
                + "\n\n📊 Order Status:\n"
                + status_msg
                + "\n\n⚠️ Warnings:\n"
                + warning_msg
            )
        else:
            final_message = main_message + "\n\n📊 Order Status:\n" + status_msg
        if virtual_guard_enabled:
            final_message += "\n\n🛡️ Protection mode: Virtual TP/SL guard active (server-managed)."
        
        return {
            "success": True, 
            "message": final_message,
            "order_status": {
                "main": "✅" if main_order_id else "⚠️",
                "sl": "✅" if sl_created else "❌",
                "tp1": "✅" if tp1_created else "❌" if tp1 > 0 else "—",
                "tp2": "✅" if tp2_created else "❌" if tp2 > 0 else "—",
            }
        }
        
    except Exception as e:
        db.session.rollback()
        log_trade_event("TRADE_FAIL", f"❌ {str(e)}", user_id)
        return {"success": False, "message": f"❌ {str(e)}"}

def partial_close_position(symbol, close_percent=None, close_qty=None, user_id=None):
    from models import TradePosition, db
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
        pos_db = TradePosition.query.filter_by(user_id=user_id, symbol=symbol, status='open').order_by(TradePosition.updated_at.desc()).first()
        if pos_db:
            base_qty = abs(float(pos_db.initial_qty or 0)) or abs(amt)
            closed_pct = (abs(q) / base_qty * 100) if base_qty else 0
            pos_db.remain_qty_pct = max(0.0, float(pos_db.remain_qty_pct or 100.0) - closed_pct)
            if pos_db.remain_qty_pct <= 0.01:
                pos_db.remain_qty_pct = 0.0
                pos_db.status = 'closed'
            db.session.commit()
        log_trade_event("PARTIAL_CLOSE", f"Closed {q} units of {symbol}, PnL: ${order.get('realizedPnl', 0):.2f}", user_id)
        
        # Fix 10: Invalidate conditional cache after partial close
        invalidate_conditional_cache(user_id)
        
        # Invalidate caches after partial close
        cache_key_positions = f"positions_{user_id or 'public'}"
        cache_key_trades = f"trade_history_{user_id or 'public'}"
        if cache_key_positions in _positions_cache:
            del _positions_cache[cache_key_positions]
        if cache_key_trades in _trade_history_cache:
            del _trade_history_cache[cache_key_trades]
        return {"success": True, "message": f"Closed {q} units", "order": order}
    
    except Exception as e:
        db.session.rollback()
        return {"success": False, "message": str(e)}

def close_position(symbol, user_id=None):
    from models import TradePosition, db
    try:
        client = get_client(user_id)
        if client is None:
            return {"success": False, "message": get_last_client_error(user_id) or "No Binance connection"}
        positions = client.futures_position_information(symbol=symbol)
        pos = next((p for p in positions if abs(float(p.get('positionAmt', 0))) > 0), None)
        if not pos: return {"success": False, "message": "No position"}
        
        amt = abs(float(pos.get('positionAmt', 0)))
        side = Client.SIDE_SELL if float(pos.get('positionAmt', 0)) > 0 else Client.SIDE_BUY
        client.futures_create_order(symbol=symbol, side=side, type="MARKET", quantity=amt)
        client.futures_cancel_all_open_orders(symbol=symbol)
        # Also cancel any open algo orders for this symbol (TP1 lives here)
        # futures_cancel_all_open_orders does NOT touch algo orders
        try:
            algo_resp = None
            if hasattr(client, 'futures_get_algo_orders'):
                algo_resp = client.futures_get_algo_orders(recvWindow=10000)
            elif hasattr(client, '_request_futures_api'):
                algo_resp = client._request_futures_api('get', 'algoOrder/openOrders', True, data={'recvWindow': 10000})
            if algo_resp is not None:
                algo_orders = algo_resp if isinstance(algo_resp, list) else algo_resp.get('orders', [])
                for ao in algo_orders:
                    if ao.get('symbol') == symbol:
                        algo_id = ao.get('algoId') or ao.get('orderId')
                        if algo_id:
                            try:
                                if hasattr(client, 'futures_cancel_algo_order'):
                                    client.futures_cancel_algo_order(algoId=algo_id)
                                elif hasattr(client, '_request_futures_api'):
                                    client._request_futures_api('delete', 'algoOrder', True, data={'algoId': algo_id})
                                print(f"[DEBUG] Cancelled algo order {algo_id} for {symbol}")
                            except Exception as _ae:
                                print(f"[DEBUG] Algo order {algo_id} cancel skipped: {_ae}")
        except Exception as _algo_ex:
            print(f"[DEBUG] Algo orders cancel on close (non-fatal): {_algo_ex}")
        pos_db = TradePosition.query.filter_by(user_id=user_id, symbol=symbol, status='open').order_by(TradePosition.updated_at.desc()).first()
        if pos_db:
            pos_db.status = 'closed'
            pos_db.remain_qty_pct = 0.0
            db.session.commit()
        log_trade_event("TRADE_CLOSE", f"Closed full position {symbol}", user_id)
        
        # Fix 10: Invalidate conditional cache after position close
        invalidate_conditional_cache(user_id)
        
        # Invalidate caches after position close
        cache_key_positions = f"positions_{user_id or 'public'}"
        cache_key_trades = f"trade_history_{user_id or 'public'}"
        if cache_key_positions in _positions_cache:
            del _positions_cache[cache_key_positions]
        if cache_key_trades in _trade_history_cache:
            del _trade_history_cache[cache_key_trades]
        return {"success": True, "message": "Position Closed"}
    except Exception as e: 
        db.session.rollback()
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

def get_trade_history(user_id=None, force_refresh=False):
    from models import TradePosition
    global _trade_history_cache, _trade_history_cache_time
    current_time = time.time()
    cache_key = f"trade_history_{user_id or 'public'}"
    
    # Return cached trade history if less than 10 seconds old
    if (not force_refresh) and cache_key in _trade_history_cache and (current_time - _trade_history_cache_time.get(cache_key, 0)) < 10:
        return _trade_history_cache[cache_key]
    
    try:
        client = get_client(user_id)
        if client is None:
            return _trade_history_cache.get(cache_key, [])
        # Increase lookback to 7 days
        start_time = int((time.time() - 7 * 24 * 3600) * 1000)
        binance_trades = client.futures_account_trades(limit=1000, startTime=start_time)
        
        trade_history = [{
            'time': datetime.fromtimestamp(t.get('time', 0)/1000).strftime("%Y-%m-%d %H:%M:%S"), 
            'symbol': t.get('symbol'), 
            'side': 'LONG' if t.get('side') == 'BUY' else 'SHORT', 
            'qty': float(t.get('qty', 0)), 
            'price': float(t.get('price', 0)), 
            'realized_pnl': float(t.get('realizedPnl', 0)), 
            'commission': float(t.get('commission', 0)), 
            'order_id': str(t.get('orderId')),
            'raw_time': t.get('time', 0)
        } for t in binance_trades]
        
        # Merge with local TradePosition records
        if user_id:
            db_positions = TradePosition.query.filter_by(user_id=user_id).all()
            existing_order_ids = {t['order_id'] for t in trade_history}
            
            for pos in db_positions:
                oid = str(pos.opening_order_id) if pos.opening_order_id else None
                if oid and oid not in existing_order_ids:
                    trade_history.append({
                        'time': pos.created_at.strftime("%Y-%m-%d %H:%M:%S") if pos.created_at else "N/A",
                        'symbol': pos.symbol,
                        'side': pos.side,
                        'qty': float(pos.initial_qty or 0),
                        'price': float(pos.entry_price or 0),
                        'realized_pnl': float(pos.unrealized_pnl or 0), # Best effort if closed
                        'commission': 0.0,
                        'order_id': oid,
                        'sl_price': float(pos.sl_price or 0),
                        'current_sl': float(pos.current_sl or 0),
                        'tp1_price': float(pos.tp1_price or 0),
                        'tp1_qty_pct': float(pos.tp1_qty_pct or 0),
                        'tp2_price': float(pos.tp2_price or 0),
                        'remain_qty_pct': float(pos.remain_qty_pct or 0),
                        'position_status': pos.status or 'open',
                        'raw_time': int(pos.created_at.timestamp() * 1000) if pos.created_at else 0
                    })
        
        # Sort by time descending
        trade_history.sort(key=lambda x: x.get('raw_time', 0), reverse=True)
        
        # Attach levels to Binance trades if not already present
        trade_history = attach_trade_levels(trade_history, user_id)
        
        # Cache the results
        _trade_history_cache[cache_key] = trade_history
        _trade_history_cache_time[cache_key] = current_time
        return trade_history
    except Exception as e:
        print(f"Error in get_trade_history: {e}")
        return []

def attach_trade_levels(trades, user_id=None):
    from models import TradePosition
    if not user_id or not trades:
        return trades
    try:
        positions = TradePosition.query.filter_by(user_id=user_id).order_by(TradePosition.created_at.desc()).limit(300).all()
        if not positions:
            return trades
        order_to_pos = {}
        for pos in positions:
            oid = getattr(pos, "opening_order_id", None) or None
            if oid:
                order_to_pos[str(oid)] = pos
        def match_position(trade):
            oid = trade.get("order_id")
            if oid is not None and str(oid) in order_to_pos:
                p = order_to_pos[str(oid)]
                if p.symbol == trade.get("symbol"):
                    return p
            symbol = trade.get('symbol')
            trade_time_str = trade.get('time')
            if not symbol or not trade_time_str:
                return None
            try:
                trade_time = datetime.strptime(trade_time_str, "%Y-%m-%d %H:%M:%S")
            except Exception:
                return None
            best = None
            best_delta = None
            for pos in positions:
                if pos.symbol != symbol or not pos.created_at:
                    continue
                delta = abs((trade_time - pos.created_at).total_seconds())
                if best is None or delta < best_delta:
                    best = pos
                    best_delta = delta
            # Ignore clearly unrelated historical matches
            if best is None or best_delta is None or best_delta > 7 * 24 * 3600:
                return None
            return best
        enriched = []
        for trade in trades:
            pos = match_position(trade)
            if pos:
                trade = dict(trade)
                trade.update({
                    'sl_price': float(pos.sl_price or 0),
                    'current_sl': float(pos.current_sl or 0),
                    'tp1_price': float(pos.tp1_price or 0),
                    'tp1_qty_pct': float(pos.tp1_qty_pct or 0),
                    'tp2_price': float(pos.tp2_price or 0),
                    'remain_qty_pct': float(pos.remain_qty_pct or 0),
                    'position_status': pos.status or 'open'
                })
            enriched.append(trade)
        return enriched
    except Exception as e:
        print(f"Error attaching TP/SL to trade history: {e}")
        return trades

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

def get_wallet_balances(user_id=None):
    """Get detailed wallet balances for user"""
    try:
        client_res = get_client(user_id)
        if not client_res:
            last_err = get_last_client_error(user_id) if user_id else None
            return {"success": False, "error": last_err or "No Binance client"}

        if isinstance(client_res, dict) and "error" in client_res:
            return {"success": False, "error": client_res["error"]}

        client = client_res
        acc = client.futures_account(recvWindow=10000)
        assets = []
        for asset in acc.get('assets', []):
            if float(asset.get('walletBalance', 0)) > 0:
                assets.append({
                    'asset': asset.get('asset'),
                    'balance': float(asset.get('walletBalance', 0)),
                    'unrealized': float(asset.get('unrealizedProfit', 0)),
                    'margin': float(asset.get('initialMargin', 0))
                })
                
        return {
            "success": True,
            "total_assets": float(acc.get('totalWalletBalance', 0)),
            "total_unrealized": float(acc.get('totalUnrealizedProfit', 0)),
            "assets": assets,
            "debug_info": {
                "totalInitialMargin": acc.get('totalInitialMargin'),
                "totalMaintMargin": acc.get('totalMaintMargin'),
                "availableBalance": acc.get('availableBalance')
            }
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

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
        if cache_age < 2:
            cached_price = _price_cache[cache_key]
            print(f"✓ PRICE CACHE HIT [{cache_age:.1f}s old]: {symbol} = ${cached_price}")
            return cached_price
        else:
            print(f"🔄 PRICE CACHE EXPIRED [{cache_age:.1f}s]: {symbol} - fetching fresh...")
    else:
        print(f"🔄 PRICE CACHE MISS: {symbol} - fetching fresh...")
    
    # ✅ ATTEMPT 1: Try client API (if user has connected exchange)
    try:
        client_res = get_client(user_id)
        if client_res and not isinstance(client_res, dict):
            client = client_res
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
    
    for endpoint in public_endpoints:
        try:
            print(f"   → Trying public endpoint: {endpoint}")
            response = requests.get(endpoint, timeout=5, proxies=proxies)
            data = response.json()
            # Handle both single ticker and list responses
            if isinstance(data, list):
                data = data[0] if len(data) > 0 else {}
            price = float(data.get('price', 0))
            if price > 0:
                _price_cache[cache_key] = price
                _price_cache_time[cache_key] = current_time
                print(f"✅ GOT PRICE FROM PUBLIC API: {symbol} = ${price}")
                return price
        except Exception as e:
            print(f"   ⚠️ Public API {endpoint} failed: {e}")
            continue

    # ✅ ATTEMPT 2b: CoinGecko public API fallback (no API key needed)
    COINGECKO_MAP = {
        'BTCUSDT': 'bitcoin', 'ETHUSDT': 'ethereum', 'BNBUSDT': 'binancecoin',
        'SOLUSDT': 'solana', 'XRPUSDT': 'ripple', 'ADAUSDT': 'cardano',
        'DOGEUSDT': 'dogecoin', 'LINKUSDT': 'chainlink', 'LTCUSDT': 'litecoin',
        'MATICUSDT': 'matic-network', 'AVAXUSDT': 'avalanche-2', 'DOTUSDT': 'polkadot'
    }
    coin_id = COINGECKO_MAP.get(symbol)
    if coin_id:
        try:
            print(f"   → Trying CoinGecko fallback for {symbol}...")
            cg_url = f"https://api.coingecko.com/api/v3/simple/price?ids={coin_id}&vs_currencies=usd"
            cg_response = requests.get(cg_url, timeout=5)
            cg_data = cg_response.json()
            price = float(cg_data.get(coin_id, {}).get('usd', 0))
            if price > 0:
                _price_cache[cache_key] = price
                _price_cache_time[cache_key] = current_time
                print(f"✅ GOT PRICE FROM COINGECKO: {symbol} = ${price}")
                return price
        except Exception as e:
            print(f"   ⚠️ CoinGecko fallback failed: {e}")
            
    # ✅ ATTEMPT 3: Last resort - use stale cache if available
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

def get_live_balance(user_id):
    """
    Fetch the real-time futures wallet balance and margin used from Binance.
    Returns a tuple ((balance, margin_used), error_msg).
    """
    try:
        client_res = get_user_exchange_client(user_id)
        if not client_res:
            return ((0.0, 0.0), "Exchange not connected - add your Binance API keys")

        if isinstance(client_res, dict) and "error" in client_res:
            return ((0.0, 0.0), client_res["error"])
            
        client = client_res
        acc = client.futures_account(recvWindow=10000)
        balance = float(acc.get('totalWalletBalance', 0.0))
        margin_used = float(acc.get('totalInitialMargin', 0.0))
        return ((balance, margin_used), None)
    except Exception as e:
        print(f"❌ Error fetching live balance for user {user_id}: {e}")
        return ((0.0, 0.0), str(e))
