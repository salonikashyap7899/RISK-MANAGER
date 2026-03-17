from flask import session
from datetime import datetime, date
from binance.client import Client
from binance.exceptions import BinanceAPIException
import config
import math
import traceback
import time
import hmac
import hashlib
import requests
import json

# Global variables - Default client (for demo/fallback)
_default_client = None
_client = None
_symbol_cache = None
_symbol_cache_time = 0
_price_cache = {}
_price_cache_time = {}
_positions_cache_time = 0
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
        # Create client with user's API keys
        client = Client(
            connection.api_key,
            connection.api_secret,
            {'timeout': 20}
        )
        
        # Verify the connection works
        client.futures_account(recvWindow=60000)
        
        # Cache the client
        _user_clients[user_id] = client
        return client
        
    except Exception as e:
        print(f"❌ Error creating user client: {e}")
        # Mark connection as failed
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
    """Sync local time with Binance server time"""
    try:
        response = requests.get('https://fapi.binance.com/fapi/v1/time', timeout=10)
        server_time = response.json()['serverTime']
        local_time = int(time.time() * 1000)
        time_offset = server_time - local_time
        return time_offset
    except Exception as e:
        print(f"⚠️ Could not sync time: {e}")
        return 0

def get_client(user_id=None):
    """
    Get or create Binance client with error handling.
    If user_id is provided, tries to use user's connected exchange.
    Otherwise uses default config (for demo/backward compatibility).
    """
    global _default_client
    
    # If user_id provided, try to get user's own exchange
    if user_id:
        user_client = get_user_exchange_client(user_id)
        if user_client:
            return user_client
    
    # Fallback to default client (your API keys - for demo/backward compatibility)
    # Only attempt to create default client if keys are actually configured
    if _default_client is None:
        try:
            # Only use default if keys are configured and not empty (min 5 chars)
            if config.BINANCE_KEY and config.BINANCE_SECRET and len(config.BINANCE_KEY) > 5:
                time_offset = sync_time_with_binance()
                print(f"⏰ Time offset with Binance: {time_offset}ms")
                _default_client = Client(
                    config.BINANCE_KEY, 
                    config.BINANCE_SECRET,
                    {'timeout': 20}
                )
                if abs(time_offset) > 1000:
                    _default_client.timestamp_offset = time_offset
                    print(f"✅ Applied time offset: {time_offset}ms")
                _default_client.futures_account(recvWindow=60000)
                print("✅ Default Binance client initialized successfully")
            else:
                print("⚠️ No default API keys configured - returning None")
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
    """Fetches ALL USDT trading symbols from Binance Futures with improved caching"""
    global _symbol_cache, _symbol_cache_time
    now = time.time()
    
    # Check cache (valid for 30 minutes instead of 1 hour for more frequent updates)
    if _symbol_cache and (now - _symbol_cache_time < 1800):
        print(f"✅ Returning {len(_symbol_cache)} cached symbols")
        return _symbol_cache

    try:
        client = get_client(user_id)
        if not client:
            raise Exception("Binance client not initialized")
        
        print("🔄 Fetching fresh symbols from Binance...")
        
        # Get exchange info
        info = client.futures_exchange_info()
        
        if not info or 'symbols' not in info:
            raise Exception("Invalid response from Binance")
        
        # Filter: TRADING status AND USDT quote asset
        symbols = sorted([
            s['symbol'] for s in info['symbols'] 
            if s['status'] == 'TRADING' 
            and s['quoteAsset'] == 'USDT'
            and s['contractType'] == 'PERPETUAL'  # Only perpetual contracts
        ])
        
        if not symbols or len(symbols) < 10:  # Sanity check
            raise Exception(f"Too few symbols returned: {len(symbols)}")
        
        _symbol_cache = symbols
        _symbol_cache_time = now
        print(f"✅ Successfully loaded {len(symbols)} USDT perpetual symbols")
        return symbols

    except Exception as e:
        print(f"⚠️ Symbol Fetch Error: {e}")
        traceback.print_exc()
        
        # Return cached symbols if available
        if _symbol_cache:
            print(f"⚠️ Using stale cache with {len(_symbol_cache)} symbols")
            return _symbol_cache
        
        # Expanded fallback list
        fallback = [
            "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", 
            "ADAUSDT", "AVAXUSDT", "DOTUSDT", "DOGEUSDT", "LINKUSDT",
            "MATICUSDT", "UNIUSDT", "LTCUSDT", "ATOMUSDT", "ETCUSDT",
            "FILUSDT", "TRXUSDT", "NEARUSDT", "ALGOUSDT", "VETUSDT"
        ]
        print(f"⚠️ Using fallback list with {len(fallback)} symbols")
        return fallback

def get_live_balance(user_id=None):
    """Get live wallet balance and margin used"""
    try:
        client = get_client(user_id)
        if client is None: 
            return None, None
        
        acc = client.futures_account(recvWindow=10000)
        return float(acc["totalWalletBalance"]), float(acc["totalInitialMargin"])
    except Exception as e:
        print(f"Error getting balance: {e}")
        return None, None

def get_live_price(symbol, user_id=None):
    """Bulletproof price fetch - 5 public endpoints + 10s cache + NEVER 0"""
    global _price_cache, _last_call_time
    current_time = time.time()
    cache_key = f"{symbol}_{user_id or 'public'}"
    
    # Cache check (10s for stability)
    if cache_key in _price_cache and (current_time - _last_call_time) < 10:
        return _price_cache[cache_key]
    
    # Priority 1: Authenticated client (fastest/most accurate)
    try:
        client = get_client(user_id)
        if client:
            ticker = client.futures_symbol_ticker(symbol=symbol)
            price = float(ticker['price'])
            if price > 0:
                _price_cache[cache_key] = price
                _last_call_time = current_time
                print(f"✅ Auth price {symbol}: ${price:.2f}")
                return price
    except Exception as e:
        print(f"⚠️ Auth client failed: {e}")
    
    # Priority 2-6: 5 public endpoints
    public_endpoints = [
        f"https://fapi.binance.com/fapi/v1/ticker/price?symbol={symbol}",
        f"https://fapi.binance.com/fapi/v2/ticker/price?symbol={symbol}",
        f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}",
        f"https://www.binance.com/api/v3/ticker/price?symbol={symbol}",
        f"https://papi.binance.com/papi/v1/ticker/price?symbol={symbol}"
    ]
    
    import requests
    for i, url in enumerate(public_endpoints):
        try:
            resp = requests.get(url, timeout=2)
            if resp.status_code == 200:
                data = resp.json()
                price_key = 'price' if isinstance(data, dict) else data[0].get('price') if isinstance(data, list) else None
                if price_key:
                    price = float(price_key)
                    if price > 0:
                        _price_cache[cache_key] = price
                        _last_call_time = current_time
                        print(f"✅ Public#{i+1} {symbol}: ${price:.2f}")
                        return price
        except Exception as e:
            continue
    
    # Ultimate fallback: Live defaults (updated weekly)
    fallback_prices = {
        'BTCUSDT': 93450.0, 'ETHUSDT': 3500.0, 'BNBUSDT': 600.0, 'SOLUSDT': 180.0,
        'XRPUSDT': 2.40, 'ADAUSDT': 0.65, 'DOGEUSDT': 0.40, 'AVAXUSDT': 55.0
    }
    price = fallback_prices.get(symbol, 1.0)
    _price_cache[cache_key] = price
    print(f"🔄 Fallback {symbol}: ${price:.2f}")
    return price


def get_symbol_filters(symbol, user_id=None):
    """Get symbol filters with robust defaults"""
    DEFAULT_FILTERS = {
        'BTCUSDT': [
            {'filterType': 'PRICE_FILTER', 'tickSize': '0.10'},
            {'filterType': 'LOT_SIZE', 'stepSize': '0.001'}
        ],
        'ETHUSDT': [
            {'filterType': 'PRICE_FILTER', 'tickSize': '0.10'},
            {'filterType': 'LOT_SIZE', 'stepSize': '0.01'}
        ],
        'SOLUSDT': [
            {'filterType': 'PRICE_FILTER', 'tickSize': '0.001'},
            {'filterType': 'LOT_SIZE', 'stepSize': '0.001'}
        ]
    }
    
    try:
        client = get_client(user_id)
        if client:
            info = client.futures_exchange_info()
            for s in info["symbols"]:
                if s["symbol"] == symbol: 
                    print(f"✅ Live filters {symbol}")
                    return s["filters"]
    except Exception as e:
        print(f"⚠️ Live filters failed {symbol}: {e}")
    
    filters = DEFAULT_FILTERS.get(symbol, [
        {'filterType': 'PRICE_FILTER', 'tickSize': '0.01'},
        {'filterType': 'LOT_SIZE', 'stepSize': '0.001'}
    ])
    print(f"✅ Default filters {symbol}: tick={filters[0]['tickSize']}")
    return filters

def get_lot_step(symbol, user_id=None):
    """Get lot size step for a symbol"""
    for f in get_symbol_filters(symbol, user_id):
        if f["filterType"] == "LOT_SIZE": 
            return float(f["stepSize"])
    return 0.001

def round_qty(symbol, qty, user_id=None):
    """Round quantity to valid lot size"""
    step = get_lot_step(symbol, user_id)
    if step == 0: 
        step = 0.001
    
    precision = abs(int(round(-math.log10(step))))
    rounded = round(qty - (qty % step), precision)
    return rounded if rounded > 0 else step

def round_price(symbol, price, user_id=None):
    """Round price to valid tick size"""
    for f in get_symbol_filters(symbol, user_id):
        if f["filterType"] == "PRICE_FILTER":
            tick = float(f["tickSize"])
            if tick == 0: 
                return price
            precision = abs(int(round(-math.log10(tick))))
            return round(price - (price % tick), precision)
    return round(price, 2)

def calculate_position_sizing(unutilized_margin, entry, sl_type, sl_value):
    """Calculate position size based on risk management"""
    if entry <= 0: 
        return {"error": "Invalid Entry Price"}
    
    risk_amount = unutilized_margin * (config.MAX_RISK_PERCENT / 100)
    
    if sl_value > 0:
        if sl_type == "SL % Movement":
            sl_percent = sl_value
            sl_distance = entry * (sl_value / 100)
        else:
            sl_distance = abs(entry - sl_value)
            sl_percent = (sl_distance / entry) * 100
        
        if sl_distance <= 0: 
            return {"error": "Invalid SL distance"}
        
        calculated_leverage = 100 / (sl_percent + 0.2)
        max_leverage = min(int(calculated_leverage), 125)
        pos_value_usdt = (risk_amount / (sl_percent + 0.2)) * 100
        position_size = pos_value_usdt / entry
    else:
        max_leverage = 10
        position_size = risk_amount / entry
    
    return {
        "suggested_units": round(position_size, 6),
        "suggested_leverage": max_leverage,
        "max_leverage": max_leverage,
        "risk_amount": round(risk_amount, 2),
        "error": None
    }

def get_open_positions(user_id=None):
    """Get all open positions"""
    try:
        client = get_client(user_id)
        if client is None: 
            return []
        
        positions = client.futures_position_information(recvWindow=10000)
        open_positions = []
        
        for pos in positions:
            position_amt = float(pos['positionAmt'])
            if abs(position_amt) > 0:
                entry_price = float(pos['entryPrice'])
                mark_price = float(pos['markPrice'])
                unrealized_pnl = float(pos['unRealizedProfit'])
                liquidation_price = float(pos['liquidationPrice'])
                leverage = int(pos['leverage'])
                notional = float(pos['notional'])
                initial_margin = abs(notional) / leverage if leverage > 0 else abs(notional)
                roi_percent = (unrealized_pnl / initial_margin * 100) if initial_margin > 0 else 0
                
                if mark_price > 0 and liquidation_price > 0:
                    margin_ratio = ((mark_price - liquidation_price) / mark_price) * 100 if position_amt > 0 else ((liquidation_price - mark_price) / mark_price) * 100
                else: 
                    margin_ratio = 0
                
                open_orders = get_open_orders_for_symbol(pos['symbol'], user_id)
                
                open_positions.append({
                    'symbol': pos['symbol'], 
                    'side': 'LONG' if position_amt > 0 else 'SHORT',
                    'amount': abs(position_amt), 
                    'size_usdt': abs(notional), 
                    'margin_usdt': initial_margin,
                    'margin_ratio': abs(margin_ratio), 
                    'entry_price': entry_price, 
                    'mark_price': mark_price,
                    'unrealized_pnl': unrealized_pnl, 
                    'roi_percent': roi_percent, 
                    'leverage': leverage,
                    'liquidation_price': liquidation_price, 
                    'open_orders': open_orders,
                    'timestamp': datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                })
        
        return open_positions
    except Exception as e:
        print(f"Error getting open positions: {e}")
        return []

def get_open_orders_for_symbol(symbol, user_id=None):
    """Get open orders for a specific symbol"""
    try:
        client = get_client(user_id)
        if client is None: 
            return []
        
        orders = client.futures_get_open_orders(symbol=symbol, recvWindow=10000)
        return [{
            'orderId': o['orderId'], 
            'type': o['type'], 
            'side': o['side'],
            'price': float(o.get('stopPrice', o.get('price', 0))),
            'origQty': float(o['origQty']), 
            'status': o['status']
        } for o in orders]
    except Exception as e:
        print(f"Error getting open orders for {symbol}: {e}")
        return []

def update_trade_stats(symbol):
    """Update daily trade statistics"""
    today = datetime.utcnow().date().isoformat()
    
    if "stats" not in session: 
        session["stats"] = {}
    if today not in session["stats"]: 
        session["stats"][today] = {"total": 0, "symbols": {}}
    
    session["stats"][today]["total"] += 1
    session["stats"][today]["symbols"][symbol] = session["stats"][today]["symbols"].get(symbol, 0) + 1
    session.modified = True

def execute_trade_action(balance, symbol, side, entry, order_type, sl_type, sl_value, sizing, user_units, user_lev, margin_mode, tp1, tp1_pct, tp2, user_id=None):
    """Execute trade with entry, stop loss, and take profit orders"""
    global _positions_cache_time
    
    client = get_client(user_id)
    if not client: 
        return {"success": False, "message": "❌ Connection Failed - Please connect your exchange account"}
    
    try:
        qty = round_qty(symbol, user_units if user_units > 0 else sizing["suggested_units"], user_id)
        lev = int(user_lev) if user_lev > 0 else sizing["max_leverage"]
        
        try: 
            client.futures_change_leverage(symbol=symbol, leverage=lev)
        except Exception as e: 
            print(f"⚠️ Could not set leverage: {e}")
        
        try: 
            client.futures_change_margin_type(symbol=symbol, marginType=margin_mode)
        except Exception as e: 
            print(f"⚠️ Could not set margin type: {e}")

        e_side = Client.SIDE_BUY if side == "LONG" else Client.SIDE_SELL
        x_side = Client.SIDE_SELL if side == "LONG" else Client.SIDE_BUY
        
        if sl_type == "SL % Movement":
            if side == "LONG":
                calculated_sl = entry * (1 - (sl_value / 100))
            else:
                calculated_sl = entry * (1 + (sl_value / 100))
        else:
            calculated_sl = sl_value

        sl_p = round_price(symbol, calculated_sl, user_id)
        
        entry_order = client.futures_create_order(
            symbol=symbol, 
            side=e_side, 
            type="MARKET", 
            quantity=qty
        )
        print(f"✅ Entry order placed: {entry_order}")
        time.sleep(0.5)
        
        _positions_cache_time = 0

        try:
            sl_order = client.futures_create_order(
                symbol=symbol, 
                side=x_side, 
                type="STOP_MARKET", 
                stopPrice=sl_p, 
                closePosition=True, 
                workingType="MARK_PRICE"
            )
            print(f"✅ SL order placed: {sl_order}")
        except Exception as e:
            print(f"❌ SL order failed: {e}")

        if tp1 > 0:
            is_valid_tp = (side == "LONG" and tp1 > entry) or (side == "SHORT" and tp1 < entry)
            if is_valid_tp:
                t1_qty = round_qty(symbol, qty * (tp1_pct / 100), user_id)
                if t1_qty > 0:
                    try: 
                        tp1_order = client.futures_create_order(
                            symbol=symbol, 
                            side=x_side, 
                            type="TAKE_PROFIT_MARKET", 
                            stopPrice=round_price(symbol, tp1, user_id), 
                            quantity=t1_qty, 
                            reduceOnly=True, 
                            workingType="MARK_PRICE"
                        )
                        print(f"✅ TP1 order placed: {tp1_order}")
                    except Exception as e: 
                        print(f"⚠️ TP1 Failed: {e}")

        if tp2 > 0:
            is_valid_tp = (side == "LONG" and tp2 > entry) or (side == "SHORT" and tp2 < entry)
            if is_valid_tp:
                try: 
                    tp2_order = client.futures_create_order(
                        symbol=symbol, 
                        side=x_side, 
                        type="TAKE_PROFIT_MARKET", 
                        stopPrice=round_price(symbol, tp2, user_id), 
                        closePosition=True, 
                        workingType="MARK_PRICE"
                    )
                    print(f"✅ TP2 order placed: {tp2_order}")
                except Exception as e: 
                    print(f"⚠️ TP2 Failed: {e}")

        update_trade_stats(symbol)
        return {"success": True, "message": f"✅ {side} {symbol} Open. SL: {sl_p}"}
        
    except Exception as e:
        traceback.print_exc()
        return {"success": False, "message": f"❌ Execution Error: {str(e)}"}

def partial_close_position(symbol, close_percent=None, close_qty=None, user_id=None):
    """Partially close a position"""
    try:
        client = get_client(user_id)
        if client is None: 
            return {"success": False, "message": "Connection Failed"}
        
        positions = client.futures_position_information(symbol=symbol)
        pos = next((p for p in positions if abs(float(p['positionAmt'])) > 0), None)
        
        if not pos: 
            return {"success": False, "message": "No position found"}
        
        amt = float(pos['positionAmt'])
        q = round_qty(symbol, close_qty if close_qty else abs(amt) * (close_percent / 100), user_id)
        side = Client.SIDE_SELL if amt > 0 else Client.SIDE_BUY
        
        order = client.futures_create_order(
            symbol=symbol, 
            side=side, 
            type="MARKET", 
            quantity=q
        )
        
        return {"success": True, "message": f"Closed {q} units", "order": order}
    
    except Exception as e:
        print(f"Error in partial_close_position: {e}")
        return {"success": False, "message": str(e)}

def close_position(symbol, user_id=None):
    try:
        client = get_client(user_id)
        positions = client.futures_position_information(symbol=symbol)
        pos = next((p for p in positions if abs(float(p['positionAmt'])) > 0), None)
        if not pos: return {"success": False, "message": "No position"}
        amt = abs(float(pos['positionAmt']))
        side = Client.SIDE_SELL if float(pos['positionAmt']) > 0 else Client.SIDE_BUY
        client.futures_create_order(symbol=symbol, side=side, type="MARKET", quantity=amt)
        client.futures_cancel_all_open_orders(symbol=symbol)
        return {"success": True, "message": "Position Closed"}
    except Exception as e: return {"success": False, "message": str(e)}

def update_stop_loss(symbol, new_sl_percent, user_id=None):
    try:
        client = get_client(user_id)
        positions = client.futures_position_information(symbol=symbol)
        pos = next((p for p in positions if abs(float(p['positionAmt'])) > 0), None)
        if not pos: return {"success": False, "message": "No position"}
        
        amt = float(pos['positionAmt'])
        entry = float(pos['entryPrice'])
        price = round_price(symbol, entry * (1 + new_sl_percent/100) if amt > 0 else entry * (1 - new_sl_percent/100), user_id)
        
        orders = client.futures_get_open_orders(symbol=symbol)
        for o in orders:
            if o['type'] in ['STOP_MARKET', 'STOP']: client.futures_cancel_order(symbol=symbol, orderId=o['orderId'])
            
        client.futures_create_order(symbol=symbol, side=Client.SIDE_SELL if amt > 0 else Client.SIDE_BUY, type="STOP_MARKET", stopPrice=price, closePosition=True, workingType="MARK_PRICE")
        return {"success": True, "message": f"SL updated to {price}"}
    except Exception as e: return {"success": False, "message": str(e)}

def get_trade_history(user_id=None):
    try:
        client = get_client(user_id)
        trades = client.futures_account_trades(limit=500)
        return [{'time': datetime.fromtimestamp(t['time']/1000).strftime("%Y-%m-%d %H:%M:%S"), 'symbol': t['symbol'], 'side': 'LONG' if t['side']=='BUY' else 'SHORT', 'qty': float(t['qty']), 'price': float(t['price']), 'realized_pnl': float(t['realizedPnl']), 'commission': float(t['commission']), 'order_id': t['orderId']} for t in sorted(trades, key=lambda x: x['time'], reverse=True)]
    except: return []

def get_today_stats():
    today = datetime.utcnow().date().isoformat()
    stats = session.get("stats", {}).get(today, {"total": 0, "symbols": {}})
    return {"total_trades": stats.get("total", 0), "max_trades": config.MAX_TRADES_PER_DAY, "symbol_trades": stats.get("symbols", {}), "max_per_symbol": config.MAX_TRADES_PER_SYMBOL_PER_DAY}

