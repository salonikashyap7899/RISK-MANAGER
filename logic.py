from flask import session
from datetime import datetime
from binance.client import Client
from binance.exceptions import BinanceAPIException
import config
import math
import traceback
import time
import requests

# Global variables - Default client (for demo/fallback)
_default_client = None
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
    Includes Proxy support to bypass Render geo-restrictions.
    """
    from models import ExchangeConnection
    import config  

    # 1. Check if we already have a cached client for this user
    if user_id in _user_clients:
        return _user_clients[user_id]
    
    # 2. Get user's exchange connection from database
    connection = ExchangeConnection.query.filter_by(
        user_id=user_id, 
        exchange_type='binance',
        is_connected=True
    ).first()
    
    if not connection or not connection.api_key or not connection.api_secret:
        return None
    
    try:
        # 3. Setup Proxy from config.py (Render Environment Variable)
        # Format must be: http://user:pass@ip:port
        proxies = {
            'http': config.PROXY_URL,
            'https': config.PROXY_URL
        } if config.PROXY_URL else None

        # 4. Initialize Binance Client with Proxy settings
        client = Client(
            connection.api_key, 
            connection.api_secret,
            requests_params={'proxies': proxies} if proxies else None
        )
        
        # 5. Sync timestamp to prevent -1021 errors
        server_time = client.get_server_time()
        client.timestamp_offset = server_time['serverTime'] - int(time.time() * 1000)
        
        # Cache and return
        _user_clients[user_id] = client
        return client

    except Exception as e:
        print(f"❌ Binance Connection Error for user {user_id}: {e}")
        traceback.print_exc()
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
    
    if _symbol_cache and (now - _symbol_cache_time < 1800):
        return _symbol_cache

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
        
    return ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"]

def get_wallet_balances(user_id=None):
    """Get detailed wallet balances (only > 0)"""
    try:
        client = get_client(user_id)
        if client is None:
            return {'success': False, 'error': 'No Binance client available'}
        
        acc = client.futures_account(recvWindow=10000)
        assets = acc.get('assets', [])
        
        balances = []
        total_usdt_equiv = 0.0
        
        for asset in assets:
            free = float(asset.get('availableBalance', 0))
            locked = float(asset.get('lockedBalance', 0))
            total = float(asset.get('walletBalance', 0))
            
            if total > 0:
                asset_name = asset.get('asset', '')
                balances.append({
                    'asset': asset_name,
                    'free': round(free, 6),
                    'locked': round(locked, 6),
                    'total': round(total, 6)
                })
                
                if asset_name == 'USDT':
                    total_usdt_equiv += total
                else:
                    live_price = get_live_price(f"{asset_name}USDT", user_id)
                    if live_price:
                        total_usdt_equiv += (total * live_price)
        
        return {
            'success': True, 
            'balances': balances, 
            'total_assets': len(balances),
            'total_usdt_equiv': round(total_usdt_equiv, 2)
        }
        
    except Exception as e:
        return {'success': False, 'error': str(e)}

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
    """Bulletproof price fetch securely converting string -> float"""
    global _price_cache, _last_call_time
    current_time = time.time()
    cache_key = f"{symbol}_{user_id or 'public'}"
    
    if cache_key in _price_cache and (current_time - _last_call_time) < 1:
        return _price_cache[cache_key]
    
    try:
        client = get_client(user_id)
        if client:
            ticker = client.futures_symbol_ticker(symbol=symbol)
            price = float(ticker.get('price', 0))
            if price > 0:
                _price_cache[cache_key] = price
                _last_call_time = current_time
                return price
    except Exception:
        pass
    
    public_endpoints = [
        f"https://fapi.binance.com/fapi/v1/ticker/price?symbol={symbol}",
        f"https://fapi.binance.com/fapi/v2/ticker/price?symbol={symbol}"
    ]
    
    for url in public_endpoints:
        try:
            resp = requests.get(url, timeout=2)
            if resp.status_code == 200:
                data = resp.json()
                price_key = data.get('price') if isinstance(data, dict) else None
                if price_key:
                    price = float(price_key)
                    if price > 0:
                        _price_cache[cache_key] = price
                        _last_call_time = current_time
                        return price
        except Exception:
            continue
    
    fallback_prices = {'BTCUSDT': 93450.0, 'ETHUSDT': 3500.0, 'BNBUSDT': 600.0, 'SOLUSDT': 180.0}
    price = fallback_prices.get(symbol, 1.0)
    _price_cache[cache_key] = price
    return price

def get_symbol_filters(symbol, user_id=None):
    DEFAULT_FILTERS = [
        {'filterType': 'PRICE_FILTER', 'tickSize': '0.01'},
        {'filterType': 'LOT_SIZE', 'stepSize': '0.001'}
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

def get_lot_step(symbol, user_id=None):
    for f in get_symbol_filters(symbol, user_id):
        if f.get("filterType") == "LOT_SIZE": 
            return float(f.get("stepSize", 0.001))
    return 0.001

def round_qty(symbol, qty, user_id=None):
    step = get_lot_step(symbol, user_id)
    if step == 0: 
        step = 0.001
    precision = abs(int(round(-math.log10(step))))
    rounded = round(qty - (qty % step), precision)
    return rounded if rounded > 0 else step

def round_price(symbol, price, user_id=None):
    for f in get_symbol_filters(symbol, user_id):
        if f.get("filterType") == "PRICE_FILTER":
            tick = float(f.get("tickSize", 0.01))
            if tick == 0: 
                return price
            precision = abs(int(round(-math.log10(tick))))
            return round(price - (price % tick), precision)
    return round(price, 2)

def calculate_position_sizing(unutilized_margin, entry, sl_type, sl_value):
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
                roi_percent = (unrealized_pnl / initial_margin * 100) if initial_margin > 0 else 0
                
                if mark_price > 0 and liquidation_price > 0:
                    margin_ratio = ((mark_price - liquidation_price) / mark_price) * 100 if position_amt > 0 else ((liquidation_price - mark_price) / mark_price) * 100
                else: 
                    margin_ratio = 0
                
                open_orders = get_open_orders_for_symbol(pos.get('symbol'), user_id)
                
                open_positions.append({
                    'symbol': pos.get('symbol'), 
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

def update_trade_stats(symbol):
    today = datetime.utcnow().date().isoformat()
    if "stats" not in session: 
        session["stats"] = {}
    if today not in session["stats"]: 
        session["stats"][today] = {"total": 0, "symbols": {}}
    
    session["stats"][today]["total"] += 1
    session["stats"][today]["symbols"][symbol] = session["stats"][today]["symbols"].get(symbol, 0) + 1
    session.modified = True

def execute_trade_action(balance, symbol, side, entry, order_type, sl_type, sl_value, sizing, user_units, user_lev, margin_mode, tp1, tp1_pct, tp2, user_id=None):
    global _positions_cache_time
    client = get_client(user_id)
    if not client: 
        return {"success": False, "message": "❌ Connection Failed - Please connect your exchange account"}
    
    try:
        qty = round_qty(symbol, user_units if user_units > 0 else sizing["suggested_units"], user_id)
        lev = int(user_lev) if user_lev > 0 else sizing["max_leverage"]
        
        try: 
            client.futures_change_leverage(symbol=symbol, leverage=lev)
            client.futures_change_margin_type(symbol=symbol, marginType=margin_mode)
        except Exception: 
            pass

        e_side = Client.SIDE_BUY if side == "LONG" else Client.SIDE_SELL
        x_side = Client.SIDE_SELL if side == "LONG" else Client.SIDE_BUY
        
        if sl_type == "SL % Movement":
            calculated_sl = entry * (1 - (sl_value / 100)) if side == "LONG" else entry * (1 + (sl_value / 100))
        else:
            calculated_sl = sl_value

        sl_p = round_price(symbol, calculated_sl, user_id)
        
        client.futures_create_order(symbol=symbol, side=e_side, type="MARKET", quantity=qty)
        time.sleep(0.5)
        _positions_cache_time = 0

        try:
            client.futures_create_order(
                symbol=symbol, side=x_side, type="STOP_MARKET", 
                stopPrice=sl_p, closePosition=True, workingType="MARK_PRICE"
            )
        except Exception as e:
            print(f"❌ SL order failed: {e}")

        if tp1 > 0 and ((side == "LONG" and tp1 > entry) or (side == "SHORT" and tp1 < entry)):
            t1_qty = round_qty(symbol, qty * (tp1_pct / 100), user_id)
            if t1_qty > 0:
                try: 
                    client.futures_create_order(
                        symbol=symbol, side=x_side, type="TAKE_PROFIT_MARKET", 
                        stopPrice=round_price(symbol, tp1, user_id), quantity=t1_qty, 
                        reduceOnly=True, workingType="MARK_PRICE"
                    )
                except Exception: pass

        if tp2 > 0 and ((side == "LONG" and tp2 > entry) or (side == "SHORT" and tp2 < entry)):
            try: 
                client.futures_create_order(
                    symbol=symbol, side=x_side, type="TAKE_PROFIT_MARKET", 
                    stopPrice=round_price(symbol, tp2, user_id), closePosition=True, 
                    workingType="MARK_PRICE"
                )
            except Exception: pass

        update_trade_stats(symbol)
        return {"success": True, "message": f"✅ {side} {symbol} Open. SL: {sl_p}"}
        
    except Exception as e:
        traceback.print_exc()
        return {"success": False, "message": f"❌ Execution Error: {str(e)}"}

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
        side = Client.SIDE_SELL if amt > 0 else Client.SIDE_BUY
        
        order = client.futures_create_order(symbol=symbol, side=side, type="MARKET", quantity=q)
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
        return {"success": True, "message": "Position Closed"}
    except Exception as e: 
        return {"success": False, "message": str(e)}

def update_stop_loss(symbol, new_sl_percent, user_id=None):
    try:
        client = get_client(user_id)
        positions = client.futures_position_information(symbol=symbol)
        pos = next((p for p in positions if abs(float(p.get('positionAmt', 0))) > 0), None)
        if not pos: return {"success": False, "message": "No position"}
        
        amt = float(pos.get('positionAmt', 0))
        entry = float(pos.get('entryPrice', 0))
        price = round_price(symbol, entry * (1 + new_sl_percent/100) if amt > 0 else entry * (1 - new_sl_percent/100), user_id)
        
        orders = client.futures_get_open_orders(symbol=symbol)
        for o in orders:
            if o.get('type') in ['STOP_MARKET', 'STOP']: 
                client.futures_cancel_order(symbol=symbol, orderId=o.get('orderId'))
            
        client.futures_create_order(
            symbol=symbol, side=Client.SIDE_SELL if amt > 0 else Client.SIDE_BUY, 
            type="STOP_MARKET", stopPrice=price, closePosition=True, workingType="MARK_PRICE"
        )
        return {"success": True, "message": f"SL updated to {price}"}
    except Exception as e: 
        return {"success": False, "message": str(e)}

def get_trade_history(user_id=None):
    try:
        client = get_client(user_id)
        trades = client.futures_account_trades(limit=500)
        return [{
            'time': datetime.fromtimestamp(t.get('time', 0)/1000).strftime("%Y-%m-%d %H:%M:%S"), 
            'symbol': t.get('symbol'), 
            'side': 'LONG' if t.get('side') == 'BUY' else 'SHORT', 
            'qty': float(t.get('qty', 0)), 
            'price': float(t.get('price', 0)), 
            'realized_pnl': float(t.get('realizedPnl', 0)), 
            'commission': float(t.get('commission', 0)), 
            'order_id': t.get('orderId')
        } for t in sorted(trades, key=lambda x: x.get('time', 0), reverse=True)]
    except Exception: 
        return []

def get_today_stats():
    today = datetime.utcnow().date().isoformat()
    stats = session.get("stats", {}).get(today, {"total": 0, "symbols": {}})
    return {
        "total_trades": stats.get("total", 0), 
        "max_trades": config.MAX_TRADES_PER_DAY, 
        "symbol_trades": stats.get("symbols", {}), 
        "max_per_symbol": config.MAX_TRADES_PER_SYMBOL_PER_DAY
    }