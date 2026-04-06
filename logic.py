# logic.py
from flask import session
from datetime import datetime
from binance.client import Client
from binance.exceptions import BinanceAPIException
import config
import math
import traceback
import time
import requests
from models import db, TradeDailyStats, TradeLog, TradePosition

_default_client = None
_symbol_cache = None
_symbol_cache_time = 0
_price_cache = {}
_price_cache_time = {}
_positions_cache = {}
_positions_cache_time = {}
_trade_history_cache = {}
_trade_history_cache_time = {}
_leverage_info_cache = {}
_leverage_info_cache_time = {}
_last_call_time = 0
CACHE_DURATION = 5

_user_clients = {}

def get_user_exchange_client(user_id):
    from models import ExchangeConnection
    import config  

    if user_id in _user_clients:
        return _user_clients[user_id]
    
    connection = ExchangeConnection.query.filter_by(
        user_id=user_id, 
        exchange_type='binance',
        is_connected=True
    ).first()
    
    if not connection or not connection.api_key or not connection.api_secret:
        return None
    
    try:
        time_offset = sync_time_with_binance()
        req_params = {'timeout': 20}
        
        if hasattr(config, 'PROXY_URL') and config.PROXY_URL:
            req_params['proxies'] = {
                'https': config.PROXY_URL, 
                'http': config.PROXY_URL
            }
        
        client = Client(
            api_key=connection.api_key,
            api_secret=connection.api_secret,
            requests_params=req_params
        )
        
        if abs(time_offset) > 100:
            client.timestamp_offset = time_offset
        
        client.futures_account(recvWindow=10000)
        _user_clients[user_id] = client
        return client
        
    except BinanceAPIException as e:
        connection.is_connected = False
        from models import db
        db.session.commit()
        return None
    except Exception as e:
        connection.is_connected = False
        from models import db
        db.session.commit()
        return None

def set_user_client(user_id, client):
    _user_clients[user_id] = client

def clear_user_client(user_id):
    if user_id in _user_clients:
        del _user_clients[user_id]

def sync_time_with_binance():
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
                return server_time - local_time
        except Exception:
            continue
    return 0

def get_client(user_id=None):
    global _default_client
    import config 
    
    if user_id:
        user_client = get_user_exchange_client(user_id)
        if user_client:
            return user_client
    
    if _default_client is None:
        try:
            if config.BINANCE_KEY and config.BINANCE_SECRET and len(config.BINANCE_KEY) > 5:
                time_offset = sync_time_with_binance()
                req_params = {'timeout': 20}
                if hasattr(config, 'PROXY_URL') and config.PROXY_URL:
                    req_params['proxies'] = {'https': config.PROXY_URL, 'http': config.PROXY_URL}
                
                _default_client = Client(api_key=config.BINANCE_KEY, api_secret=config.BINANCE_SECRET, requests_params=req_params)
                if abs(time_offset) > 100:  
                    _default_client.timestamp_offset = time_offset
                _default_client.futures_account(recvWindow=10000)
            else:
                return None
        except Exception:
            _default_client = None
            return None
    return _default_client

def initialize_session():
    if "trades" not in session:
        session["trades"] = []
    if "stats" not in session:
        session["stats"] = {}
    session.modified = True

def get_all_exchange_symbols(user_id=None):
    global _symbol_cache, _symbol_cache_time
    now = time.time()
    try:
        client = get_client(user_id)
        if not client: raise Exception("Binance client not initialized")
        info = client.futures_exchange_info()
        symbols = sorted([s['symbol'] for s in info.get('symbols', []) if s['status'] == 'TRADING' and s['quoteAsset'] == 'USDT' and s['contractType'] == 'PERPETUAL'])
        if len(symbols) > 0:
            _symbol_cache = symbols
            _symbol_cache_time = now
            return symbols
    except Exception as e:
        print(f"⚠️ Symbol Fetch Error: {e}")
    return ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"]

def get_max_leverage_for_symbol(symbol, user_id=None):
    """
    NEW FEATURE: Fetch maximum available leverage from Binance for a specific symbol.
    Returns: int (max leverage like 20, 50, 75, 125) or None on error
    """
    global _leverage_info_cache, _leverage_info_cache_time
    
    cache_key = f"{symbol}_{user_id}"
    now = time.time()
    
    # Check cache (valid for 1 hour)
    if cache_key in _leverage_info_cache:
        if (now - _leverage_info_cache_time.get(cache_key, 0)) < 3600:
            return _leverage_info_cache[cache_key]
    
    try:
        client = get_client(user_id)
        if not client:
            return 125  # Default fallback
        
        # Fetch leverage brackets for the symbol
        brackets = client.futures_leverage_bracket(symbol=symbol)
        
        if brackets and len(brackets) > 0:
            # Get the first bracket which contains max leverage info
            symbol_bracket = brackets[0]
            max_leverage = symbol_bracket.get('brackets', [{}])[0].get('initialLeverage', 125)
            
            # Cache the result
            _leverage_info_cache[cache_key] = max_leverage
            _leverage_info_cache_time[cache_key] = now
            
            return max_leverage
        
        return 125  # Default if no data found
        
    except Exception as e:
        print(f"⚠️ Max Leverage Fetch Error for {symbol}: {e}")
        return 125  # Safe default

def get_wallet_balances(user_id=None):
    try:
        from models import ExchangeConnection
        connection = ExchangeConnection.query.filter_by(user_id=user_id, exchange_type='binance', is_connected=True).first()
        if not connection:
            return {'success': False, 'error': 'No Binance connection found', 'balances': [], 'total_assets': 0}
        client = get_client(user_id)
        if client is None:
            return {'success': False, 'error': 'Client not initialized', 'balances': [], 'total_assets': 0}

        account = client.futures_account(recvWindow=10000)
        assets = account.get('assets', [])
        balances = []
        total_usdt_equiv = 0.0

        for asset in assets:
            try:
                asset_name = asset.get('asset', '')
                wallet_balance = float(asset.get('walletBalance', '0'))
                available_balance = float(asset.get('availableBalance', '0'))
                if wallet_balance <= 0: continue

                balances.append({
                    'asset': asset_name,
                    'wallet_balance': wallet_balance,
                    'available_balance': available_balance
                })
                
                if asset_name == 'USDT':
                    total_usdt_equiv += wallet_balance
                else:
                    try:
                        ticker_symbol = f"{asset_name}USDT"
                        ticker = client.futures_symbol_ticker(symbol=ticker_symbol)
                        price = float(ticker.get('price', 0))
                        total_usdt_equiv += wallet_balance * price
                    except:
                        total_usdt_equiv += wallet_balance
            except Exception as e:
                continue

        return {'success': True, 'balances': balances, 'total_assets': round(total_usdt_equiv, 2)}
    except Exception as e:
        return {'success': False, 'error': str(e), 'balances': [], 'total_assets': 0}

def get_live_balance(user_id=None):
    try:
        client = get_client(user_id)
        if client is None: return (0, 0)
        account = client.futures_account(recvWindow=10000)
        balance = float(account.get('totalWalletBalance', 0))
        margin = float(account.get('totalMarginBalance', 0))
        return (balance, margin)
    except: return (0, 0)

def get_live_price(symbol, user_id=None):
    global _price_cache, _price_cache_time
    try:
        now = time.time()
        if symbol in _price_cache and (now - _price_cache_time.get(symbol, 0)) < CACHE_DURATION:
            return _price_cache[symbol]
        client = get_client(user_id)
        if client is None: return None
        ticker = client.futures_symbol_ticker(symbol=symbol)
        price = float(ticker.get('price', 0))
        _price_cache[symbol] = price
        _price_cache_time[symbol] = now
        return price
    except: return None

def round_price(symbol, price, user_id=None):
    try:
        client = get_client(user_id)
        if not client: return round(price, 2)
        info = client.futures_exchange_info()
        symbol_info = next((s for s in info.get('symbols', []) if s['symbol'] == symbol), None)
        if symbol_info:
            filters = symbol_info.get('filters', [])
            price_filter = next((f for f in filters if f['filterType'] == 'PRICE_FILTER'), None)
            if price_filter:
                tick_size = float(price_filter.get('tickSize', '0.01'))
                precision = int(round(-math.log10(tick_size)))
                return round(price / tick_size) * tick_size
        return round(price, 2)
    except: return round(price, 2)

def round_qty(symbol, qty, user_id=None):
    try:
        client = get_client(user_id)
        if not client: return round(qty, 3)
        info = client.futures_exchange_info()
        symbol_info = next((s for s in info.get('symbols', []) if s['symbol'] == symbol), None)
        if symbol_info:
            filters = symbol_info.get('filters', [])
            lot_filter = next((f for f in filters if f['filterType'] == 'LOT_SIZE'), None)
            if lot_filter:
                step_size = float(lot_filter.get('stepSize', '1'))
                precision = int(round(-math.log10(step_size)))
                return round(qty / step_size) * step_size
        return round(qty, 3)
    except: return round(qty, 3)

def calculate_position_sizing(capital, entry, sl_type, sl_value, side, user_id=None, symbol="BTCUSDT"):
    """
    ENHANCED FEATURE: Calculate position sizing with mandatory SL validation
    Returns comprehensive sizing info including max available leverage from exchange
    """
    import config
    
    # MANDATORY SL CHECK
    if not sl_value or sl_value <= 0:
        return {
            'error': '🚨 SL is MANDATORY! You must set a Stop Loss to trade.',
            'risk_amount': 0,
            'suggested_units': 0,
            'suggested_leverage': 0,
            'max_exchange_leverage': 0,
            'sl_price': 0,
            'sl_percent': 0,
            'can_trade': False
        }
    
    if not capital or capital <= 0 or not entry or entry <= 0:
        return {
            'error': 'Invalid capital or entry price',
            'risk_amount': 0,
            'suggested_units': 0,
            'suggested_leverage': 0,
            'max_exchange_leverage': 0,
            'sl_price': 0,
            'sl_percent': 0,
            'can_trade': False
        }
    
    # Calculate SL price based on input type
    if sl_type == "SL % Movement":
        sl_percent = abs(float(sl_value))
        if side == "LONG":
            sl_price = entry * (1 - sl_percent / 100)
        else:
            sl_price = entry * (1 + sl_percent / 100)
    else:  # SL Price
        sl_price = float(sl_value)
        sl_percent = abs((entry - sl_price) / entry * 100)
    
    # Validate SL direction
    if side == "LONG" and sl_price >= entry:
        return {
            'error': '❌ LONG positions need SL BELOW entry price!',
            'risk_amount': 0,
            'suggested_units': 0,
            'suggested_leverage': 0,
            'max_exchange_leverage': 0,
            'sl_price': sl_price,
            'sl_percent': sl_percent,
            'can_trade': False
        }
    
    if side == "SHORT" and sl_price <= entry:
        return {
            'error': '❌ SHORT positions need SL ABOVE entry price!',
            'risk_amount': 0,
            'suggested_units': 0,
            'suggested_leverage': 0,
            'max_exchange_leverage': 0,
            'sl_price': sl_price,
            'sl_percent': sl_percent,
            'can_trade': False
        }
    
    # Get max leverage available from exchange for this symbol
    max_exchange_leverage = get_max_leverage_for_symbol(symbol, user_id)
    
    # Fixed 1% risk as per requirement
    risk_percent = config.RISK_PER_TRADE  # Always 1.0%
    risk_amount = capital * (risk_percent / 100)
    
    # Calculate safe leverage with buffer
    buffer = 0.2  # 0.2% buffer for fees and slippage
    safe_leverage = math.floor(100 / (sl_percent + buffer))
    
    # Apply exchange max leverage constraint
    suggested_leverage = max(1, min(safe_leverage, max_exchange_leverage))
    
    # Calculate position size
    position_value = risk_amount / ((sl_percent + buffer) / 100)
    suggested_units = position_value / entry
    
    # Round to exchange precision
    suggested_units = round_qty(symbol, suggested_units, user_id)
    sl_price = round_price(symbol, sl_price, user_id)
    
    return {
        'risk_amount': round(risk_amount, 2),
        'suggested_units': suggested_units,
        'suggested_leverage': suggested_leverage,
        'max_exchange_leverage': max_exchange_leverage,
        'sl_price': sl_price,
        'sl_percent': round(sl_percent, 2),
        'can_trade': True,
        'leverage_warning': suggested_leverage >= max_exchange_leverage
    }

def get_open_positions(user_id=None):
    global _positions_cache, _positions_cache_time
    current_time = time.time()
    cache_key = f"positions_{user_id or 'public'}"
    
    if cache_key in _positions_cache and (current_time - _positions_cache_time.get(cache_key, 0)) < 10:
        return _positions_cache[cache_key]
    
    try:
        client = get_client(user_id)
        if not client: return []
        positions = client.futures_position_information()
        open_positions = []
        
        for pos in positions:
            amt = float(pos.get('positionAmt', 0))
            if abs(amt) > 0:
                entry_price = float(pos.get('entryPrice', 0))
                mark_price = float(pos.get('markPrice', 0))
                unrealized_pnl = float(pos.get('unRealizedProfit', 0))
                
                # Calculate ROI percentage
                leverage = int(pos.get('leverage', 1))
                position_value = abs(amt) * entry_price
                margin_used = position_value / leverage if leverage > 0 else position_value
                roi_pct = (unrealized_pnl / margin_used * 100) if margin_used > 0 else 0
                
                open_positions.append({
                    'symbol': pos.get('symbol'),
                    'side': 'LONG' if amt > 0 else 'SHORT',
                    'entry_price': entry_price,
                    'mark_price': mark_price,
                    'position_amt': abs(amt),
                    'unrealized_pnl': unrealized_pnl,
                    'roi_pct': round(roi_pct, 2),
                    'leverage': leverage,
                    'liquidation_price': float(pos.get('liquidationPrice', 0))
                })
        
        _positions_cache[cache_key] = open_positions
        _positions_cache_time[cache_key] = current_time
        return open_positions
    except Exception as e:
        print(f"Error fetching positions: {e}")
        return []

def get_today_stats(user_id):
    today_str = datetime.utcnow().strftime("%Y-%m-%d")
    stats_obj = TradeDailyStats.get_for_user(user_id, today_str)
    
    symbol_trades = stats_obj.get_symbol_trades()
    
    return {
        'total_trades': stats_obj.total_trades,
        'max_trades': config.MAX_TRADES_PER_DAY,
        'symbol_trades': symbol_trades,
        'max_per_symbol': config.MAX_TRADES_PER_SYMBOL_PER_DAY
    }

def update_trade_stats(symbol, user_id):
    today_str = datetime.utcnow().strftime("%Y-%m-%d")
    stats_obj = TradeDailyStats.get_for_user(user_id, today_str)
    
    stats_obj.total_trades += 1
    
    symbol_trades = stats_obj.get_symbol_trades()
    symbol_trades[symbol] = symbol_trades.get(symbol, 0) + 1
    stats_obj.set_symbol_trades(symbol_trades)
    
    db.session.commit()

def can_place_trade(symbol, user_id):
    """
    ENHANCED: Check if user can place trade based on daily limits
    """
    today_stats = get_today_stats(user_id)
    
    if today_stats['total_trades'] >= today_stats['max_trades']:
        return False, f"❌ Daily limit reached: {today_stats['total_trades']}/{today_stats['max_trades']} trades"
    
    symbol_count = today_stats['symbol_trades'].get(symbol, 0)
    if symbol_count >= today_stats['max_per_symbol']:
        return False, f"❌ Symbol limit reached: {symbol_count}/{today_stats['max_per_symbol']} trades on {symbol}"
    
    return True, "OK"

def place_trade_with_1pct_risk(symbol, side, entry, sl_type, sl_value, tp1=0, tp1_pct=0, tp2=0, order_type="MARKET", margin_mode="ISOLATED", user_id=None):
    """
    ENHANCED TRADE EXECUTION with all new features:
    - Mandatory SL validation
    - Max leverage checking from exchange
    - 1% fixed risk enforcement
    - TP1 and TP2 with partial exits
    - Daily trade limits
    """
    import config
    
    try:
        # FEATURE 1: Mandatory SL check
        if not sl_value or sl_value <= 0:
            return {"success": False, "message": "🚨 STOP LOSS IS MANDATORY! Cannot trade without SL."}
        
        # FEATURE 6 & 7: Check daily limits
        can_trade, limit_msg = can_place_trade(symbol, user_id)
        if not can_trade:
            return {"success": False, "message": limit_msg}
        
        client = get_client(user_id)
        if not client:
            return {"success": False, "message": "No connection to exchange"}
        
        balance, margin = get_live_balance(user_id)
        unutilized = max(balance - margin, 0)
        
        if unutilized <= 0:
            return {"success": False, "message": "Insufficient balance"}
        
        # FEATURE 3 & 4: Calculate with mandatory SL and get max leverage
        sizing = calculate_position_sizing(unutilized, entry, sl_type, sl_value, side, user_id, symbol)
        
        if not sizing.get('can_trade'):
            return {"success": False, "message": sizing.get('error', 'Invalid sizing')}
        
        calculated_sl = sizing['sl_price']
        qty = sizing['suggested_units']
        lev = sizing['suggested_leverage']
        max_exchange_lev = sizing['max_exchange_leverage']
        
        # FEATURE 5: Validate leverage against exchange maximum
        if lev > max_exchange_lev:
            return {
                "success": False, 
                "message": f"❌ Calculated leverage {lev}x exceeds Binance max {max_exchange_lev}x for {symbol}. Reduce position or widen SL."
            }
        
        # Validate minimum notional
        min_notional = 5.0
        if qty * entry < min_notional:
            return {"success": False, "message": f"Position too small. Min ${min_notional} required."}
        
        # Set leverage and margin mode
        client.futures_change_leverage(symbol=symbol, leverage=lev)
        client.futures_change_margin_type(symbol=symbol, marginType=margin_mode)
        
        # Cancel existing orders
        try:
            client.futures_cancel_all_open_orders(symbol=symbol)
        except:
            pass
        
        # Place main order
        qty = round_qty(symbol, qty, user_id)
        sl_p = round_price(symbol, calculated_sl, user_id)
        x_side = Client.SIDE_SELL if side == "LONG" else Client.SIDE_BUY
        
        order_params = {
            "symbol": symbol,
            "side": Client.SIDE_BUY if side == "LONG" else Client.SIDE_SELL,
            "type": order_type,
            "quantity": qty
        }
        
        if order_type == "LIMIT":
            order_params["price"] = round_price(symbol, entry, user_id)
            order_params["timeInForce"] = "GTC"
        
        client.futures_create_order(**order_params)
        time.sleep(0.5)
        
        # Place SL order (MANDATORY)
        client.futures_create_order(
            symbol=symbol,
            side=x_side,
            type="STOP_MARKET",
            stopPrice=sl_p,
            closePosition=True,
            workingType="MARK_PRICE"
        )
        
        # FEATURE 8: Place TP1 with partial exit
        if tp1 > 0 and tp1_pct > 0:
            if (side == "LONG" and tp1 > entry) or (side == "SHORT" and tp1 < entry):
                t1_qty = round_qty(symbol, qty * (tp1_pct / 100), user_id)
                if t1_qty > 0:
                    client.futures_create_order(
                        symbol=symbol,
                        side=x_side,
                        type="TAKE_PROFIT_MARKET",
                        stopPrice=round_price(symbol, tp1, user_id),
                        quantity=t1_qty,
                        reduceOnly=True,
                        workingType="MARK_PRICE"
                    )
        
        # FEATURE 8: Place TP2 (closes remaining position)
        if tp2 > 0:
            if (side == "LONG" and tp2 > entry) or (side == "SHORT" and tp2 < entry):
                client.futures_create_order(
                    symbol=symbol,
                    side=x_side,
                    type="TAKE_PROFIT_MARKET",
                    stopPrice=round_price(symbol, tp2, user_id),
                    closePosition=True,
                    workingType="MARK_PRICE"
                )
        
        # Save to database with leverage info
        pos = TradePosition(
            user_id=user_id,
            symbol=symbol,
            side=side,
            entry_price=entry,
            initial_qty=qty,
            sl_price=calculated_sl,
            tp1_price=tp1 if tp1 > 0 else None,
            tp1_qty_pct=tp1_pct if tp1_pct > 0 else 0,
            tp2_price=tp2 if tp2 > 0 else None,
            current_sl=calculated_sl,
            suggested_leverage=lev  # Store the leverage used
        )
        db.session.add(pos)
        db.session.commit()
        
        update_trade_stats(symbol, user_id)
        
        log_msg = f"✅ {side} {symbol} @ ${entry:.4f} | SL: ${sl_p:.4f} | Qty: {qty} | Lev: {lev}x (Max: {max_exchange_lev}x)"
        log_trade_event("TRADE_OPEN", log_msg, user_id)
        
        # Clear cache
        cache_key_pos = f"positions_{user_id}"
        cache_key_hist = f"trade_history_{user_id}"
        if cache_key_pos in _positions_cache:
            del _positions_cache[cache_key_pos]
        if cache_key_hist in _trade_history_cache:
            del _trade_history_cache[cache_key_hist]
        
        return {
            "success": True,
            "message": f"✅ {side} {symbol} placed! Lev: {lev}x | Max: {max_exchange_lev}x"
        }
        
    except Exception as e:
        db.session.rollback()
        error_msg = str(e)
        log_trade_event("TRADE_FAIL", f"❌ {error_msg}", user_id)
        return {"success": False, "message": f"❌ {error_msg}"}

def partial_close_position(symbol, close_percent=None, close_qty=None, user_id=None):
    """
    FEATURE 9: Partial profit booking / position exit
    """
    try:
        client = get_client(user_id)
        if client is None:
            return {"success": False, "message": "Connection Failed"}
        
        positions = client.futures_position_information(symbol=symbol)
        pos = next((p for p in positions if abs(float(p.get('positionAmt', 0))) > 0), None)
        if not pos:
            return {"success": False, "message": "No position found"}
        
        amt = float(pos.get('positionAmt', 0))
        
        if close_qty:
            q = round_qty(symbol, close_qty, user_id)
        elif close_percent:
            q = round_qty(symbol, abs(amt) * (close_percent / 100), user_id)
        else:
            return {"success": False, "message": "Specify close_percent or close_qty"}
        
        if q <= 0:
            return {"success": False, "message": "❌ Partial close amount too small"}
        
        side = Client.SIDE_SELL if amt > 0 else Client.SIDE_BUY
        
        order = client.futures_create_order(
            symbol=symbol,
            side=side,
            type="MARKET",
            quantity=q,
            reduceOnly=True
        )
        
        realized_pnl = float(order.get('realizedPnl', 0))
        
        log_trade_event(
            "PARTIAL_CLOSE",
            f"Partial close: {q} units of {symbol}, PnL: ${realized_pnl:.2f}",
            user_id,
            pnl=realized_pnl
        )
        
        # Update database
        pos_db = TradePosition.query.filter_by(
            user_id=user_id,
            symbol=symbol,
            status='open'
        ).first()
        
        if pos_db:
            remaining_pct = ((abs(amt) - q) / pos_db.initial_qty) * 100
            pos_db.remain_qty_pct = max(0, remaining_pct)
            db.session.commit()
        
        # Clear cache
        cache_key_pos = f"positions_{user_id}"
        cache_key_hist = f"trade_history_{user_id}"
        if cache_key_pos in _positions_cache:
            del _positions_cache[cache_key_pos]
        if cache_key_hist in _trade_history_cache:
            del _trade_history_cache[cache_key_hist]
        
        return {
            "success": True,
            "message": f"✅ Closed {q} units | PnL: ${realized_pnl:.2f}",
            "realized_pnl": realized_pnl,
            "closed_qty": q
        }
        
    except Exception as e:
        return {"success": False, "message": str(e)}

def close_position(symbol, user_id=None):
    """
    FEATURE 9: Full position exit
    """
    try:
        client = get_client(user_id)
        positions = client.futures_position_information(symbol=symbol)
        pos = next((p for p in positions if abs(float(p.get('positionAmt', 0))) > 0), None)
        
        if not pos:
            return {"success": False, "message": "No position"}
        
        amt = abs(float(pos.get('positionAmt', 0)))
        side = Client.SIDE_SELL if float(pos.get('positionAmt', 0)) > 0 else Client.SIDE_BUY
        
        order = client.futures_create_order(
            symbol=symbol,
            side=side,
            type="MARKET",
            quantity=amt
        )
        
        client.futures_cancel_all_open_orders(symbol=symbol)
        
        realized_pnl = float(order.get('realizedPnl', 0))
        
        log_trade_event(
            "TRADE_CLOSE",
            f"Full close: {symbol} | PnL: ${realized_pnl:.2f}",
            user_id,
            pnl=realized_pnl
        )
        
        # Update database
        pos_db = TradePosition.query.filter_by(
            user_id=user_id,
            symbol=symbol,
            status='open'
        ).first()
        
        if pos_db:
            pos_db.status = 'closed'
            pos_db.remain_qty_pct = 0
            db.session.commit()
        
        # Clear cache
        cache_key_pos = f"positions_{user_id}"
        cache_key_hist = f"trade_history_{user_id}"
        if cache_key_pos in _positions_cache:
            del _positions_cache[cache_key_pos]
        if cache_key_hist in _trade_history_cache:
            del _trade_history_cache[cache_key_hist]
        
        return {
            "success": True,
            "message": f"✅ Position closed | PnL: ${realized_pnl:.2f}",
            "realized_pnl": realized_pnl
        }
        
    except Exception as e:
        return {"success": False, "message": str(e)}

def trail_stop_loss(symbol, user_id=None):
    """
    FEATURE 10: Trailing SL with -1% to 0% (or positive) range only
    """
    import config
    
    try:
        pos_db = TradePosition.query.filter_by(
            user_id=user_id,
            symbol=symbol,
            status='open'
        ).first()
        
        if not pos_db:
            return {"success": False, "message": "No tracked position"}
        
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
        
        # FEATURE 10: Max trail loss is -1%, can only be -1% to 0% or positive
        max_trail_loss_pct = config.MAX_TRAIL_LOSS_PCT / 100.0  # -1% = 0.01
        
        if side == 'LONG':
            # For LONG: SL should trail up, but never go below entry - 1%
            loss_cap = entry * (1 - max_trail_loss_pct)  # Entry - 1%
            
            # Calculate new trailing SL (follows price up)
            new_sl = max(current_sl, mark * (1 - max_trail_loss_pct))
            
            # Ensure SL doesn't go below the -1% cap from entry
            new_sl = max(new_sl, loss_cap)
            
            # Keep SL below current price with small buffer
            new_sl = min(new_sl, mark * 0.998)
            
        else:  # SHORT
            # For SHORT: SL should trail down, but never go above entry + 1%
            loss_cap = entry * (1 + max_trail_loss_pct)  # Entry + 1%
            
            # Calculate new trailing SL (follows price down)
            new_sl = min(current_sl, mark * (1 + max_trail_loss_pct))
            
            # Ensure SL doesn't go above the +1% cap from entry
            new_sl = min(new_sl, loss_cap)
            
            # Keep SL above current price with small buffer
            new_sl = max(new_sl, mark * 1.002)
        
        new_sl = round_price(symbol, new_sl, user_id)
        move_pct = abs((new_sl - current_sl) / entry * 100)
        
        # Only update if movement is significant (>0.05%)
        if move_pct > 0.05:
            # Cancel old SL and place new one
            try:
                client.futures_cancel_all_open_orders(symbol=symbol, orderType='STOP_MARKET')
            except:
                pass
            
            x_side = Client.SIDE_SELL if side == 'LONG' else Client.SIDE_BUY
            client.futures_create_order(
                symbol=symbol,
                side=x_side,
                type="STOP_MARKET",
                stopPrice=new_sl,
                closePosition=True,
                workingType="MARK_PRICE"
            )
            
            pos_db.update_trail_sl(new_sl)
            db.session.commit()
            
            log_trade_event(
                "TRAIL_SL",
                f"{symbol}: SL {current_sl:.4f} → {new_sl:.4f} ({move_pct:+.2f}%)",
                user_id
            )
            
            return {
                "success": True,
                "sl_old": current_sl,
                "sl_new": new_sl,
                "move_pct": f"{move_pct:+.2f}%"
            }
        
        return {"success": True, "message": "SL optimal - no update needed"}
        
    except Exception as e:
        db.session.rollback()
        return {"success": False, "message": str(e)}

def get_live_pnl(symbol, user_id=None):
    """
    ENHANCED: Live PnL with ROI calculation and detailed tracking
    """
    try:
        pos_db = TradePosition.query.filter_by(
            user_id=user_id,
            symbol=symbol,
            status='open'
        ).order_by(TradePosition.updated_at.desc()).first()
        
        if not pos_db:
            return {"success": False, "error": "No position record"}
        
        client = get_client(user_id)
        unrealized = 0
        
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
        
        # Calculate ROI based on actual margin used
        leverage = pos_db.suggested_leverage or 1
        position_value = pos_db.initial_qty * pos_db.entry_price
        margin_used = position_value / leverage
        
        roi_pct = (unrealized / margin_used * 100) if margin_used > 0 else 0
        
        return {
            "success": True,
            "pnl": unrealized,
            "roi_pct": round(roi_pct, 2),
            "status": pos_db.status,
            "sl_current": pos_db.current_sl,
            "entry_price": pos_db.entry_price,
            "quantity": pos_db.initial_qty,
            "leverage": leverage,
            "remaining_pct": pos_db.remain_qty_pct
        }
        
    except Exception as e:
        return {"success": False, "error": str(e)}

def get_trade_history(user_id=None):
    global _trade_history_cache, _trade_history_cache_time
    current_time = time.time()
    cache_key = f"trade_history_{user_id or 'public'}"
    
    if cache_key in _trade_history_cache and (current_time - _trade_history_cache_time.get(cache_key, 0)) < 60:
        return _trade_history_cache[cache_key]
    
    try:
        client = get_client(user_id)
        trades = client.futures_account_trades(limit=500)
        
        trade_history = [{
            'time': datetime.fromtimestamp(t.get('time', 0) / 1000).strftime("%Y-%m-%d %H:%M:%S"),
            'symbol': t.get('symbol'),
            'side': 'LONG' if t.get('side') == 'BUY' else 'SHORT',
            'qty': float(t.get('qty', 0)),
            'price': float(t.get('price', 0)),
            'realized_pnl': float(t.get('realizedPnl', 0)),
            'commission': float(t.get('commission', 0)),
            'order_id': t.get('orderId')
        } for t in sorted(trades, key=lambda x: x.get('time', 0), reverse=True)]
        
        _trade_history_cache[cache_key] = trade_history
        _trade_history_cache_time[cache_key] = current_time
        return trade_history
        
    except Exception:
        return []

def log_trade_event(event_type, message, user_id=None, pnl=0.0):
    """
    ENHANCED: Live trade logging for real-time monitoring
    """
    if user_id:
        log_entry = TradeLog(
            user_id=user_id,
            event_type=event_type,
            message=message,
            pnl=float(pnl)
        )
        db.session.add(log_entry)
        db.session.commit()
    
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
    """
    ENHANCED: Get live trade events with PnL tracking
    """
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
    
    session_events = session.get("trade_events", [])[:20]
    return events[:10] + session_events[:10]