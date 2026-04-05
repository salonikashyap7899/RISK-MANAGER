from flask import session
from datetime import datetime, date
from binance.client import Client
from binance.exceptions import BinanceAPIException
import config
import math
import traceback
import time
import requests
from models import db, TradeDailyStats, TradeLog, TradePosition
import json

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
            if s['symbol'].endswith('USDT') and s.get('status') == 'TRADING'
        ])
        
        print(f"✅ Fetched {len(symbols)} USDT Perpetual symbols")
        _symbol_cache = symbols
        _symbol_cache_time = now
        return symbols
        
    except Exception as e:
        print(f"❌ Error fetching symbols: {e}")
        if _symbol_cache:
            return _symbol_cache
        return ['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'ADAUSDT', 'XRPUSDT']

def get_live_price(symbol, user_id=None):
    """Get current mark price for a symbol"""
    global _price_cache, _price_cache_time
    now = time.time()
    cache_key = f"price_{symbol}_{user_id or 'public'}"
    
    # Return cached price if less than 5 seconds old
    if cache_key in _price_cache and (now - _price_cache_time.get(cache_key, 0)) < 5:
        return _price_cache[cache_key]
    
    try:
        client = get_client(user_id)
        if not client:
            return None
        
        ticker = client.futures_mark_price(symbol=symbol)
        price = float(ticker.get('markPrice', 0))
        
        if price > 0:
            _price_cache[cache_key] = price
            _price_cache_time[cache_key] = now
            return price
        return None
        
    except Exception as e:
        print(f"Error getting price for {symbol}: {e}")
        return None

def get_max_leverage(symbol, user_id=None):
    """Fetch maximum available leverage for a symbol from Binance"""
    global _leverage_cache, _leverage_cache_time
    now = time.time()
    cache_key = f"leverage_{symbol}_{user_id or 'public'}"
    
    # Return cached leverage if less than 1 hour old
    if cache_key in _leverage_cache and (now - _leverage_cache_time.get(cache_key, 0)) < 3600:
        return _leverage_cache[cache_key]
    
    try:
        client = get_client(user_id)
        if not client:
            return 125  # Default to 125x if no client
        
        # Get leverage brackets for the symbol
        brackets = client.futures_leverage_bracket(symbol=symbol)
        
        if brackets and isinstance(brackets, list) and len(brackets) > 0:
            # Usually the first item contains the leverage info
            bracket_info = brackets[0]
            max_lev = bracket_info.get('maxLeverage', 125)
            
            _leverage_cache[cache_key] = max_lev
            _leverage_cache_time[cache_key] = now
            print(f"✅ Max leverage for {symbol}: {max_lev}x")
            return max_lev
        
        # Fallback
        return 125
        
    except Exception as e:
        print(f"⚠️ Error fetching leverage for {symbol}: {e}")
        return 125  # Default fallback

def get_symbol_info(symbol, user_id=None):
    """Get symbol precision info"""
    try:
        client = get_client(user_id)
        if not client:
            return None
        
        info = client.futures_exchange_info()
        sym_info = next((s for s in info.get('symbols', []) if s['symbol'] == symbol), None)
        return sym_info
        
    except Exception as e:
        return None

def round_price(symbol, price, user_id=None):
    """Round price to symbol's precision"""
    try:
        sym_info = get_symbol_info(symbol, user_id)
        if sym_info:
            precision = next((f['pricePrecision'] for f in sym_info.get('filters', []) if f['filterType'] == 'PRICE_FILTER'), 8)
            return round(float(price), precision)
        return round(float(price), 8)
    except Exception:
        return round(float(price), 8)

def round_qty(symbol, qty, user_id=None):
    """Round quantity to symbol's precision"""
    try:
        sym_info = get_symbol_info(symbol, user_id)
        if sym_info:
            precision = next((f['stepSize'] for f in sym_info.get('filters', []) if f['filterType'] == 'LOT_SIZE'), '0.01')
            step = float(precision)
            return math.floor(float(qty) / step) * step
        return round(float(qty), 4)
    except Exception:
        return round(float(qty), 4)

def calculate_position_sizing(unutilized_balance, entry_price, sl_type, sl_value, side):
    """
    Calculate position size based on 1% risk rule
    
    Args:
        unutilized_balance: Available capital for trading
        entry_price: Entry price of the trade
        sl_type: "SL Price" or "SL % Movement"
        sl_value: SL price or SL percentage
        side: "LONG" or "SHORT"
    
    Returns:
        Dict with suggested_units, suggested_leverage, risk_amount, sl_price, calculated_lev
    """
    try:
        if not entry_price or entry_price <= 0:
            return {
                'suggested_units': 0,
                'suggested_leverage': 1,
                'risk_amount': 0,
                'sl_price': 0,
                'calculated_lev': 1,
                'error': 'Invalid entry price'
            }
        
        # Calculate SL price
        if sl_type == "SL Price":
            sl_price = float(sl_value)
        else:  # SL % Movement
            sl_pct = float(sl_value) / 100.0
            if side == "LONG":
                sl_price = entry_price * (1 - sl_pct)
            else:
                sl_price = entry_price * (1 + sl_pct)
        
        # Calculate risk amount (1% of unutilized balance)
        risk_amount = unutilized_balance * 0.01
        
        # Calculate loss per unit
        if side == "LONG":
            loss_per_unit = entry_price - sl_price
        else:
            loss_per_unit = sl_price - entry_price
        
        if loss_per_unit <= 0:
            return {
                'suggested_units': 0,
                'suggested_leverage': 1,
                'risk_amount': 0,
                'sl_price': sl_price,
                'calculated_lev': 1,
                'error': 'Invalid SL - must be below entry for LONG, above for SHORT'
            }
        
        # Calculate quantity based on risk
        quantity = risk_amount / loss_per_unit
        
        # Calculate required leverage
        position_value = quantity * entry_price
        if position_value > 0 and unutilized_balance > 0:
            calculated_lev = position_value / unutilized_balance
        else:
            calculated_lev = 1
        
        # Cap at 125x
        calculated_lev = min(calculated_lev, 125)
        
        return {
            'suggested_units': round(quantity, 4),
            'suggested_leverage': round(calculated_lev, 2),
            'risk_amount': round(risk_amount, 2),
            'sl_price': round(sl_price, 8),
            'calculated_lev': round(calculated_lev, 2),
            'error': None
        }
        
    except Exception as e:
        print(f"Error calculating position sizing: {e}")
        return {
            'suggested_units': 0,
            'suggested_leverage': 1,
            'risk_amount': 0,
            'sl_price': 0,
            'calculated_lev': 1,
            'error': str(e)
        }

def validate_daily_limits(user_id, symbol):
    """
    Check if user can place a trade based on daily limits
    Returns: (can_trade: bool, message: str, remaining_total: int, remaining_symbol: int)
    """
    try:
        today_str = date.today().strftime('%Y-%m-%d')
        stats = TradeDailyStats.get_for_user(user_id, today_str)
        
        # Check total trades limit
        if stats.total_trades >= config.MAX_DAILY_TRADES:
            return False, f"❌ Daily limit reached ({config.MAX_DAILY_TRADES} trades)", 0, 0
        
        # Check symbol-specific limit
        symbol_trades = stats.get_symbol_trades()
        symbol_count = symbol_trades.get(symbol, 0)
        
        if symbol_count >= config.MAX_SYMBOL_TRADES:
            return False, f"❌ {symbol} limit reached ({config.MAX_SYMBOL_TRADES} trades/day)", \
                   config.MAX_DAILY_TRADES - stats.total_trades, 0
        
        remaining_total = config.MAX_DAILY_TRADES - stats.total_trades
        remaining_symbol = config.MAX_SYMBOL_TRADES - symbol_count
        
        return True, "✅ Trade allowed", remaining_total, remaining_symbol
        
    except Exception as e:
        print(f"Error validating daily limits: {e}")
        return False, f"Error checking limits: {e}", 0, 0

def validate_leverage(calculated_lev, max_available_lev):
    """
    Validate that calculated leverage doesn't exceed max available
    Returns: (valid: bool, message: str, effective_lev: float)
    """
    effective_lev = min(calculated_lev, max_available_lev)
    
    if calculated_lev > max_available_lev:
        return False, f"❌ Calculated leverage {calculated_lev:.2f}x exceeds max {max_available_lev}x", effective_lev
    
    return True, f"✅ Leverage {effective_lev:.2f}x OK (Max: {max_available_lev}x)", effective_lev

def get_today_stats(user_id):
    """Get today's trade statistics"""
    try:
        today_str = date.today().strftime('%Y-%m-%d')
        stats = TradeDailyStats.get_for_user(user_id, today_str)
        
        symbol_trades_dict = stats.get_symbol_trades()
        
        return {
            'total_trades': stats.total_trades,
            'max_trades': config.MAX_DAILY_TRADES,
            'symbol_trades': symbol_trades_dict,
            'max_per_symbol': config.MAX_SYMBOL_TRADES,
            'date': today_str
        }
    except Exception as e:
        print(f"Error getting today stats: {e}")
        return {
            'total_trades': 0,
            'max_trades': config.MAX_DAILY_TRADES,
            'symbol_trades': {},
            'max_per_symbol': config.MAX_SYMBOL_TRADES,
            'date': date.today().strftime('%Y-%m-%d')
        }

def increment_trade_count(user_id, symbol):
    """Increment trade counts after successful trade execution"""
    try:
        today_str = date.today().strftime('%Y-%m-%d')
        stats = TradeDailyStats.get_for_user(user_id, today_str)
        
        stats.total_trades += 1
        symbol_trades = stats.get_symbol_trades()
        symbol_trades[symbol] = symbol_trades.get(symbol, 0) + 1
        stats.set_symbol_trades(symbol_trades)
        
        db.session.commit()
        print(f"✅ Updated trade count for {user_id}: {stats.total_trades} total, {symbol_trades[symbol]} for {symbol}")
        
    except Exception as e:
        print(f"Error incrementing trade count: {e}")
        db.session.rollback()

def place_order(symbol, side, qty, entry_price, sl_price, tp1_price, tp1_qty_pct, tp2_price, leverage, order_type="MARKET", user_id=None):
    """
    Place a trade order with SL and TP
    
    Returns: {success, order_id, message, error}
    """
    try:
        # Validate daily limits
        can_trade, limit_msg, remaining_total, remaining_symbol = validate_daily_limits(user_id, symbol)
        if not can_trade:
            return {'success': False, 'message': limit_msg, 'order_id': None}
        
        client = get_client(user_id)
        if not client:
            return {'success': False, 'message': 'No Binance connection', 'order_id': None}
        
        # Set leverage
        try:
            client.futures_change_leverage(symbol=symbol, leverage=int(leverage))
            print(f"✅ Set leverage to {leverage}x for {symbol}")
        except Exception as e:
            print(f"⚠️ Could not set leverage: {e}")
        
        # Round quantities
        qty = round_qty(symbol, qty, user_id)
        sl_price = round_price(symbol, sl_price, user_id)
        tp1_price = round_price(symbol, tp1_price, user_id) if tp1_price > 0 else 0
        tp2_price = round_price(symbol, tp2_price, user_id) if tp2_price > 0 else 0
        
        if qty <= 0:
            return {'success': False, 'message': 'Quantity too small after rounding', 'order_id': None}
        
        # Place market order
        order = client.futures_create_order(
            symbol=symbol,
            side=side,
            type=order_type,
            quantity=qty
        )
        
        order_id = order.get('orderId')
        
        # Place SL order
        sl_side = "SELL" if side == "BUY" else "BUY"
        try:
            sl_order = client.futures_create_order(
                symbol=symbol,
                side=sl_side,
                type="STOP_MARKET",
                quantity=qty,
                stopPrice=sl_price,
                closePosition=False,
                workingType="MARK_PRICE"
            )
            print(f"✅ SL order placed at {sl_price}")
        except Exception as e:
            print(f"⚠️ SL order failed: {e}")
        
        # Place TP orders if specified
        if tp1_price > 0:
            tp1_qty = round_qty(symbol, qty * (tp1_qty_pct / 100), user_id)
            try:
                tp_side = "SELL" if side == "BUY" else "BUY"
                tp_order = client.futures_create_order(
                    symbol=symbol,
                    side=tp_side,
                    type="TAKE_PROFIT_MARKET",
                    quantity=tp1_qty,
                    stopPrice=tp1_price,
                    workingType="MARK_PRICE"
                )
                print(f"✅ TP1 order placed at {tp1_price}")
            except Exception as e:
                print(f"⚠️ TP1 order failed: {e}")
        
        if tp2_price > 0:
            tp2_qty = qty - (round_qty(symbol, qty * (tp1_qty_pct / 100), user_id) if tp1_price > 0 else 0)
            try:
                tp_side = "SELL" if side == "BUY" else "BUY"
                tp_order = client.futures_create_order(
                    symbol=symbol,
                    side=tp_side,
                    type="TAKE_PROFIT_MARKET",
                    quantity=tp2_qty,
                    stopPrice=tp2_price,
                    workingType="MARK_PRICE"
                )
                print(f"✅ TP2 order placed at {tp2_price}")
            except Exception as e:
                print(f"⚠️ TP2 order failed: {e}")
        
        # Record in database
        trade_pos = TradePosition(
            user_id=user_id,
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            initial_qty=qty,
            sl_price=sl_price,
            current_sl=sl_price,
            tp1_price=tp1_price,
            tp1_qty_pct=tp1_qty_pct,
            tp2_price=tp2_price,
            suggested_leverage=leverage
        )
        db.session.add(trade_pos)
        db.session.commit()
        
        # Increment trade count
        increment_trade_count(user_id, symbol)
        
        log_trade_event("TRADE_OPEN", f"Opened {symbol} {side} {qty} units at {entry_price} (SL: {sl_price}, TP1: {tp1_price}, TP2: {tp2_price})", user_id)
        
        return {
            'success': True,
            'message': f'✅ Trade opened: {qty} {symbol} {side}',
            'order_id': order_id,
            'remaining_total': remaining_total - 1,
            'remaining_symbol': remaining_symbol - 1
        }
        
    except Exception as e:
        print(f"Error placing order: {e}")
        traceback.print_exc()
        return {'success': False, 'message': f'Order error: {str(e)}', 'order_id': None}

def get_live_balance(user_id=None):
    """Get live wallet balance and margin used"""
    try:
        client = get_client(user_id)
        if not client:
            return ((0.0, 0.0), None)
        
        account = client.futures_account()
        total_balance = float(account.get('totalWalletBalance', 0))
        total_margin = float(account.get('totalMarginLevel', 0))
        used_margin = float(account.get('totalMaintainanceMargin', 0)) if account.get('totalMaintainanceMargin') else 0
        
        return ((total_balance, used_margin), account)
        
    except Exception as e:
        print(f"Error getting live balance: {e}")
        return ((0.0, 0.0), None)

def get_wallet_balances(user_id=None):
    """Get complete wallet info"""
    try:
        client = get_client(user_id)
        if not client:
            return {
                'success': False,
                'error': 'No Binance client configured',
                'total_assets': 0,
                'debug_info': {}
            }
        
        account = client.futures_account()
        
        total_balance = float(account.get('totalWalletBalance', 0))
        total_margin = float(account.get('totalMaintainanceMargin', 0)) if account.get('totalMaintainanceMargin') else 0
        
        return {
            'success': True,
            'total_balance': total_balance,
            'used_margin': total_margin,
            'total_assets': total_balance,
            'debug_info': {}
        }
        
    except Exception as e:
        print(f"Wallet error: {e}")
        return {
            'success': False,
            'error': str(e),
            'total_assets': 0,
            'debug_info': {'exception': str(e)}
        }

def get_positions(user_id=None):
    """Get open positions"""
    global _positions_cache, _positions_cache_time
    current_time = time.time()
    cache_key = f"positions_{user_id or 'public'}"
    
    # Return cached positions if less than 5 seconds old
    if cache_key in _positions_cache and (current_time - _positions_cache_time.get(cache_key, 0)) < 5:
        return _positions_cache[cache_key]
    
    try:
        client = get_client(user_id)
        if not client:
            return []
        
        positions = client.futures_position_information()
        open_positions = [p for p in positions if float(p.get('positionAmt', 0)) != 0]
        
        formatted = []
        for pos in open_positions:
            formatted.append({
                'symbol': pos.get('symbol'),
                'side': 'LONG' if float(pos.get('positionAmt', 0)) > 0 else 'SHORT',
                'amount': abs(float(pos.get('positionAmt', 0))),
                'entry_price': float(pos.get('entryPrice', 0)),
                'mark_price': float(pos.get('markPrice', 0)),
                'pnl': float(pos.get('unRealizedProfit', 0)),
                'pnl_pct': float(pos.get('percentage', 0)),
                'leverage': float(pos.get('leverage', 1))
            })
        
        _positions_cache[cache_key] = formatted
        _positions_cache_time[cache_key] = current_time
        return formatted
        
    except Exception as e:
        print(f"Error fetching positions: {e}")
        return []

def close_partial_position(symbol, close_percent, user_id=None):
    """Close partial position"""
    try:
        client = get_client(user_id)
        if not client:
            return {"success": False, "message": "No connection"}
        
        positions = client.futures_position_information(symbol=symbol)
        pos = next((p for p in positions if abs(float(p.get('positionAmt', 0))) > 0), None)
        if not pos:
            return {"success": False, "message": "No position"}
        
        amt = abs(float(pos.get('positionAmt', 0)))
        close_qty = amt * (close_percent / 100)
        q = round_qty(symbol, close_qty if close_qty else abs(amt) * (close_percent / 100), user_id)
        if q <= 0:
            return {"success": False, "message": "Partial close amount is too small for Binance minimum size."}
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
    """Close full position"""
    try:
        client = get_client(user_id)
        positions = client.futures_position_information(symbol=symbol)
        pos = next((p for p in positions if abs(float(p.get('positionAmt', 0))) > 0), None)
        if not pos: 
            return {"success": False, "message": "No position"}
        
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
    """Get trade history"""
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