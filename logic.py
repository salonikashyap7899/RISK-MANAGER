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

_client = None
_symbol_cache = None
_symbol_cache_time = 0
_price_cache = {}
_price_cache_time = {}
CACHE_DURATION = 5  # Cache duration in seconds


# At the top of logic.py
_price_cache = {}
_last_call_time = 0



def binance_algo_order(symbol, side, order_type, stopPrice, quantity=None, closePosition=False):
    """Universal ALGO order compatible with all Binance libraries"""
    url = "https://fapi.binance.com/fapi/v1/order"
    timestamp = int(time.time() * 1000)
    params = {
        "symbol": symbol,
        "side": side,
        "type": order_type,
        "stopPrice": stopPrice,
        "timestamp": timestamp
    }
    if quantity:
        params["quantity"] = quantity
    if closePosition:
        params["closePosition"] = "true"

    query_string = "&".join([f"{k}={v}" for k, v in params.items()])
    signature = hmac.new(
        config.BINANCE_SECRET.encode(),
        query_string.encode(),
        hashlib.sha256
    ).hexdigest()

    params["signature"] = signature
    headers = {"X-MBX-APIKEY": config.BINANCE_KEY}
    r = requests.post(url, params=params, headers=headers)
    data = r.json()
    print("🚀 ALGO ORDER RESPONSE:", data)
    if "orderId" in data:
        return {"success": True, "orderId": data["orderId"], "raw": data}
    return {"success": False, "error": data}

def sync_time_with_binance():
    """Sync local time with Binance server time"""
    try:
        response = requests.get('https://fapi.binance.com/fapi/v1/time')
        server_time = response.json()['serverTime']
        local_time = int(time.time() * 1000)
        time_offset = server_time - local_time
        return time_offset
    except Exception as e:
        print(f"⚠️ Could not sync time: {e}")
        return 0

def get_client():
    """Get or create Binance client with error handling"""
    global _client
    if _client is None:
        try:
            time_offset = sync_time_with_binance()
            print(f"⏰ Time offset with Binance: {time_offset}ms")
            _client = Client(
                config.BINANCE_KEY, 
                config.BINANCE_SECRET,
                {'timeout': 20}
            )
            if abs(time_offset) > 1000:
                _client.timestamp_offset = time_offset
                print(f"✅ Applied time offset: {time_offset}ms")
            _client.futures_account(recvWindow=60000)
            print("✅ Binance client initialized successfully")
        except Exception as e:
            print(f"❌ Error initializing Binance client: {e}")
            _client = None
    return _client

def initialize_session():
    if "trades" not in session:
        session["trades"] = []
    if "stats" not in session:
        session["stats"] = {}
    session.modified = True

def get_all_exchange_symbols():
    """Get symbols with caching"""
    global _symbol_cache, _symbol_cache_time
    current_time = time.time()
    if _symbol_cache and (current_time - _symbol_cache_time) < 3600:
        return _symbol_cache
    try:
        client = get_client()
        if client is None: return ["BTCUSDT", "ETHUSDT"]
        info = client.futures_exchange_info()
        symbols = sorted([s["symbol"] for s in info["symbols"] if s["status"] == "TRADING" and s["quoteAsset"] == "USDT"])
        _symbol_cache = symbols
        _symbol_cache_time = current_time
        return symbols
    except Exception as e:
        print(f"Error getting symbols: {e}")
        return _symbol_cache if _symbol_cache else ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"]

def get_live_balance():
    try:
        client = get_client()
        if client is None: return None, None
        acc = client.futures_account(recvWindow=10000)
        return float(acc["totalWalletBalance"]), float(acc["totalInitialMargin"])
    except Exception as e:
        print(f"Error getting balance: {e}")
        return None, None

def get_live_price(symbol):
    global _price_cache, _last_call_time
    current_time = time.time()
    
    # Only hit Binance API if cache is older than 2 seconds
    if symbol in _price_cache and (current_time - _last_call_time) < 2:
        return _price_cache[symbol]
    
    try:
        client = get_client()
        ticker = client.futures_symbol_ticker(symbol=symbol)
        price = float(ticker['price'])
        
        # Update cache
        _price_cache[symbol] = price
        _last_call_time = current_time
        return price
    except Exception as e:
        print(f"Error fetching price: {e}")
        return _price_cache.get(symbol, 0)

def get_symbol_filters(symbol):
    try:
        client = get_client()
        if client is None: return []
        info = client.futures_exchange_info()
        for s in info["symbols"]:
            if s["symbol"] == symbol: return s["filters"]
    except: pass
    return []

def get_lot_step(symbol):
    for f in get_symbol_filters(symbol):
        if f["filterType"] == "LOT_SIZE": return float(f["stepSize"])
    return 0.001

def round_qty(symbol, qty):
    step = get_lot_step(symbol)
    if step == 0: step = 0.001
    precision = abs(int(round(-math.log10(step))))
    rounded = round(qty - (qty % step), precision)
    return rounded if rounded > 0 else step

def round_price(symbol, price):
    for f in get_symbol_filters(symbol):
        if f["filterType"] == "PRICE_FILTER":
            tick = float(f["tickSize"])
            if tick == 0: return price
            precision = abs(int(round(-math.log10(tick))))
            return round(price - (price % tick), precision)
    return round(price, 2)

def calculate_position_sizing(unutilized_margin, entry, sl_type, sl_value):
    if entry <= 0: return {"error": "Invalid Entry"}
    risk_amount = unutilized_margin * (config.MAX_RISK_PERCENT / 100)
    if sl_value > 0:
        if sl_type == "SL % Movement":
            sl_percent = sl_value
            sl_distance = entry * (sl_value / 100)
        else:
            sl_distance = abs(entry - sl_value)
            sl_percent = (sl_distance / entry) * 100
        if sl_distance <= 0: return {"error": "Invalid SL distance"}
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

def get_open_positions():
    try:
        client = get_client()
        if client is None: return []
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
                else: margin_ratio = 0
                open_orders = get_open_orders_for_symbol(pos['symbol'])
                open_positions.append({
                    'symbol': pos['symbol'], 'side': 'LONG' if position_amt > 0 else 'SHORT',
                    'amount': abs(position_amt), 'size_usdt': abs(notional), 'margin_usdt': initial_margin,
                    'margin_ratio': abs(margin_ratio), 'entry_price': entry_price, 'mark_price': mark_price,
                    'unrealized_pnl': unrealized_pnl, 'roi_percent': roi_percent, 'leverage': leverage,
                    'liquidation_price': liquidation_price, 'open_orders': open_orders,
                    'timestamp': datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                })
        return open_positions
    except Exception as e:
        print(f"Error getting open positions: {e}")
        return []

def get_open_orders_for_symbol(symbol):
    try:
        client = get_client()
        if client is None: return []
        orders = client.futures_get_open_orders(symbol=symbol, recvWindow=10000)
        return [{
            'orderId': o['orderId'], 'type': o['type'], 'side': o['side'],
            'price': float(o.get('stopPrice', o.get('price', 0))),
            'origQty': float(o['origQty']), 'status': o['status']
        } for o in orders]
    except Exception as e:
        print(f"Error getting open orders for {symbol}: {e}")
        return []

def update_trade_stats(symbol):
    today = datetime.utcnow().date().isoformat()
    if "stats" not in session: session["stats"] = {}
    if today not in session["stats"]: session["stats"][today] = {"total": 0, "symbols": {}}
    session["stats"][today]["total"] += 1
    session["stats"][today]["symbols"][symbol] = session["stats"][today]["symbols"].get(symbol, 0) + 1
    session.modified = True
def execute_trade_action(balance, symbol, side, entry, order_type, sl_type, sl_value, sizing, user_units, user_lev, margin_mode, tp1, tp1_pct, tp2):
    """
    FIXED: Handles SL % Calculation and Entry + Protection Orders
    """
    client = get_client()
    if not client: return {"success": False, "message": "❌ Connection Failed"}
    
    try:
        # 1. SETUP (Leverage & Margin)
        qty = round_qty(symbol, user_units if user_units > 0 else sizing["suggested_units"])
        lev = int(user_lev) if user_lev > 0 else sizing["max_leverage"]
        
        try: client.futures_change_leverage(symbol=symbol, leverage=lev)
        except: pass # Ignore if already set
        
        try: client.futures_change_margin_type(symbol=symbol, marginType=margin_mode)
        except: pass # Ignore if already set

        # 2. DETERMINE SIDES
        e_side = Client.SIDE_BUY if side == "LONG" else Client.SIDE_SELL
        x_side = Client.SIDE_SELL if side == "LONG" else Client.SIDE_BUY
        
        # 3. CALCULATE STOP LOSS PRICE (The Fix)
        # If user selected %, calculate the price. If Price, use directly.
        if sl_type == "SL % Movement":
            if side == "LONG":
                calculated_sl = entry * (1 - (sl_value / 100))
            else: # SHORT
                calculated_sl = entry * (1 + (sl_value / 100))
        else:
            calculated_sl = sl_value

        sl_p = round_price(symbol, calculated_sl)
        
        # 4. MARKET ENTRY
        client.futures_create_order(symbol=symbol, side=e_side, type="MARKET", quantity=qty)
        time.sleep(0.5) # Short buffer to ensure entry fills
        
        # Reset cache so UI updates immediately
        global _positions_cache_time
        _positions_cache_time = 0

        # 5. PLACE STOP LOSS (Priority)
        # uses closePosition=True to protect entire size automatically
        client.futures_create_order(
            symbol=symbol, 
            side=x_side, 
            type="STOP_MARKET", 
            stopPrice=sl_p, 
            closePosition=True, 
            workingType="MARK_PRICE"
        )

        # 6. PLACE TAKE PROFITS
        # TP1 (Partial Close)
        if tp1 > 0:
            # logic to ensure TP is on correct side of entry
            is_valid_tp = (side == "LONG" and tp1 > entry) or (side == "SHORT" and tp1 < entry)
            if is_valid_tp:
                t1_qty = round_qty(symbol, qty * (tp1_pct / 100))
                # Only place if qty is valid
                if t1_qty > 0:
                    try: 
                        client.futures_create_order(
                            symbol=symbol, 
                            side=x_side, 
                            type="TAKE_PROFIT_MARKET", 
                            stopPrice=round_price(symbol, tp1), 
                            quantity=t1_qty, 
                            reduceOnly=True, 
                            workingType="MARK_PRICE"
                        )
                    except Exception as e: print(f"⚠️ TP1 Failed: {e}")

        # TP2 (Final Close)
        if tp2 > 0:
            is_valid_tp = (side == "LONG" and tp2 > entry) or (side == "SHORT" and tp2 < entry)
            if is_valid_tp:
                try: 
                    client.futures_create_order(
                        symbol=symbol, 
                        side=x_side, 
                        type="TAKE_PROFIT_MARKET", 
                        stopPrice=round_price(symbol, tp2), 
                        closePosition=True, 
                        workingType="MARK_PRICE"
                    )
                except Exception as e: print(f"⚠️ TP2 Failed: {e}")

        update_trade_stats(symbol)
        return {"success": True, "message": f"✅ {side} {symbol} Open. SL: {sl_p}"}
        
    except Exception as e:
        traceback.print_exc()
        return {"success": False, "message": f"❌ Execution Error: {str(e)}"}
def partial_close_position(symbol, close_percent=None, close_qty=None):
    try:
        client = get_client()
        positions = client.futures_position_information(symbol=symbol)
        pos = next((p for p in positions if abs(float(p['positionAmt'])) > 0), None)
        if not pos: return {"success": False, "message": "No position"}
        
        amt = float(pos['positionAmt'])
        q = round_qty(symbol, close_qty if close_qty else abs(amt) * (close_percent / 100))
        side = Client.SIDE_SELL if amt > 0 else Client.SIDE_BUY
        order = client.futures_create_order(symbol=symbol, side=side, type="MARKET", quantity=q)
        return {"success": True, "message": f"Closed {q} units"}
    except Exception as e: return {"success": False, "message": str(e)}

def close_position(symbol):
    try:
        client = get_client()
        positions = client.futures_position_information(symbol=symbol)
        pos = next((p for p in positions if abs(float(p['positionAmt'])) > 0), None)
        if not pos: return {"success": False, "message": "No position"}
        amt = abs(float(pos['positionAmt']))
        side = Client.SIDE_SELL if float(pos['positionAmt']) > 0 else Client.SIDE_BUY
        client.futures_create_order(symbol=symbol, side=side, type="MARKET", quantity=amt)
        client.futures_cancel_all_open_orders(symbol=symbol)
        return {"success": True, "message": "Position Closed"}
    except Exception as e: return {"success": False, "message": str(e)}

def update_stop_loss(symbol, new_sl_percent):
    try:
        client = get_client()
        positions = client.futures_position_information(symbol=symbol)
        pos = next((p for p in positions if abs(float(p['positionAmt'])) > 0), None)
        if not pos: return {"success": False, "message": "No position"}
        
        amt = float(pos['positionAmt'])
        entry = float(pos['entryPrice'])
        price = round_price(symbol, entry * (1 + new_sl_percent/100) if amt > 0 else entry * (1 - new_sl_percent/100))
        
        orders = client.futures_get_open_orders(symbol=symbol)
        for o in orders:
            if o['type'] in ['STOP_MARKET', 'STOP']: client.futures_cancel_order(symbol=symbol, orderId=o['orderId'])
            
        client.futures_create_order(symbol=symbol, side=Client.SIDE_SELL if amt > 0 else Client.SIDE_BUY, type="STOP_MARKET", stopPrice=price, closePosition=True, workingType="MARK_PRICE")
        return {"success": True, "message": f"SL updated to {price}"}
    except Exception as e: return {"success": False, "message": str(e)}

def get_trade_history():
    try:
        client = get_client()
        trades = client.futures_account_trades(limit=500)
        return [{'time': datetime.fromtimestamp(t['time']/1000).strftime("%Y-%m-%d %H:%M:%S"), 'symbol': t['symbol'], 'side': 'LONG' if t['side']=='BUY' else 'SHORT', 'qty': float(t['qty']), 'price': float(t['price']), 'realized_pnl': float(t['realizedPnl']), 'commission': float(t['commission']), 'order_id': t['orderId']} for t in sorted(trades, key=lambda x: x['time'], reverse=True)]
    except: return []

def get_today_stats():
    today = datetime.utcnow().date().isoformat()
    stats = session.get("stats", {}).get(today, {"total": 0, "symbols": {}})
    return {"total_trades": stats.get("total", 0), "max_trades": config.MAX_TRADES_PER_DAY, "symbol_trades": stats.get("symbols", {}), "max_per_symbol": config.MAX_TRADES_PER_SYMBOL_PER_DAY}