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

    headers = {
        "X-MBX-APIKEY": config.BINANCE_KEY
    }

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
        if client is None: 
            return ["BTCUSDT", "ETHUSDT"]
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
    """Get price with caching"""
    global _price_cache, _price_cache_time
    
    current_time = time.time()
    if symbol in _price_cache and (current_time - _price_cache_time.get(symbol, 0)) < CACHE_DURATION:
        return _price_cache[symbol]
    
    try:
        client = get_client()
        if client is None: return None
        price = float(client.futures_symbol_ticker(symbol=symbol)["price"])
        _price_cache[symbol] = price
        _price_cache_time[symbol] = current_time
        return price
    except Exception as e:
        print(f"Error getting price for {symbol}: {e}")
        return _price_cache.get(symbol, None)

def get_symbol_filters(symbol):
    try:
        client = get_client()
        if client is None: return []
        info = client.futures_exchange_info()
        for s in info["symbols"]:
            if s["symbol"] == symbol: return s["filters"]
    except:
        pass
    return []

def get_lot_step(symbol):
    for f in get_symbol_filters(symbol):
        if f["filterType"] == "LOT_SIZE": 
            return float(f["stepSize"])
    return 0.001

def round_qty(symbol, qty):
    step = get_lot_step(symbol)
    if step == 0:
        step = 0.001
    if step >= 1:
        return max(1, int(qty))
    precision = abs(int(round(-math.log10(step))))
    rounded = round(qty - (qty % step), precision)
    return rounded if rounded > 0 else step

def round_price(symbol, price):
    for f in get_symbol_filters(symbol):
        if f["filterType"] == "PRICE_FILTER":
            tick = float(f["tickSize"])
            if tick == 0:
                return price
            if tick >= 1:
                return int(price)
            precision = abs(int(round(-math.log10(tick))))
            return round(price - (price % tick), precision)
    return round(price, 2)

def calculate_position_sizing(unutilized_margin, entry, sl_type, sl_value):
    if entry <= 0: 
        return {"error": "Invalid Entry"}
    
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

def get_open_positions():
    try:
        client = get_client()
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
                    if position_amt > 0:
                        margin_ratio = ((mark_price - liquidation_price) / mark_price) * 100
                    else:
                        margin_ratio = ((liquidation_price - mark_price) / mark_price) * 100
                else:
                    margin_ratio = 0
                
                open_orders = get_open_orders_for_symbol(pos['symbol'])
                
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
        traceback.print_exc()
        return []

def get_open_orders_for_symbol(symbol):
    try:
        client = get_client()
        if client is None:
            return []
        
        orders = client.futures_get_open_orders(symbol=symbol, recvWindow=10000)
        order_list = []
        
        for order in orders:
            order_list.append({
                'orderId': order['orderId'],
                'type': order['type'],
                'side': order['side'],
                'price': float(order.get('stopPrice', order.get('price', 0))),
                'origQty': float(order['origQty']),
                'status': order['status']
            })
        
        return order_list
    except Exception as e:
        print(f"Error getting open orders for {symbol}: {e}")
        return []

def check_trade_limits(symbol):
    today = datetime.utcnow().date().isoformat()
    stats = session.get("stats", {}).get(today, {"total": 0, "symbols": {}})
    
    if stats["total"] >= config.MAX_TRADES_PER_DAY:
        return False, f"❌ Daily limit reached ({config.MAX_TRADES_PER_DAY} trades)"
    
    symbol_count = stats.get("symbols", {}).get(symbol, 0)
    if symbol_count >= config.MAX_TRADES_PER_SYMBOL_PER_DAY:
        return False, f"❌ Symbol limit reached ({config.MAX_TRADES_PER_SYMBOL_PER_DAY} trades for {symbol} today)"
    
    return True, "OK"

def update_trade_stats(symbol):
    today = datetime.utcnow().date().isoformat()
    if "stats" not in session:
        session["stats"] = {}
    if today not in session["stats"]:
        session["stats"][today] = {"total": 0, "symbols": {}}
    
    session["stats"][today]["total"] += 1
    
    if symbol not in session["stats"][today]["symbols"]:
        session["stats"][today]["symbols"][symbol] = 0
    session["stats"][today]["symbols"][symbol] += 1
    
    session.modified = True

def execute_trade_action(
    balance, symbol, side, entry, order_type,
    sl_type, sl_value, sizing,
    user_units, user_lev, margin_mode,
    tp1, tp1_pct, tp2
):
    """FORCED EXECUTION: Entry + Immediate SL + Immediate TPs"""
    client = get_client()
    if not client:
        return {"success": False, "message": "❌ Binance Connection Failed"}

    try:
        # 1. Prepare Settings
        units = user_units if user_units > 0 else sizing["suggested_units"]
        qty = round_qty(symbol, units)
        leverage = int(user_lev) if user_lev > 0 else sizing["max_leverage"]
        
        # Set Leverage & Margin
        client.futures_change_leverage(symbol=symbol, leverage=leverage)
        try:
            client.futures_change_margin_type(symbol=symbol, marginType=margin_mode)
        except: pass

        # 2. PLACE ENTRY (MARKET)
        entry_side = Client.SIDE_BUY if side == "LONG" else Client.SIDE_SELL
        exit_side = Client.SIDE_SELL if side == "LONG" else Client.SIDE_BUY
        
        entry_order = client.futures_create_order(
            symbol=symbol, 
            side=entry_side, 
            type="MARKET", 
            quantity=qty
        )
        
        # Critical: Get the exact fill price from Binance
        time.sleep(0.5) # Short pause for engine sync
        actual_entry = float(client.futures_mark_price(symbol=symbol)["markPrice"])

        # 3. ATTACH SL (PRIORITY #1)
        # Using closePosition=True ensures it never fails due to quantity/decimal errors
        sl_price = round_price(symbol, sl_value)
        
        sl_order = client.futures_create_order(
            symbol=symbol,
            side=exit_side,
            type="STOP_MARKET",
            stopPrice=sl_price,
            closePosition=True,
            workingType="MARK_PRICE",  # Avoids "trigger immediately" errors
            timeInForce="GTC"
        )

        # 4. ATTACH TP1 (PARTIAL)
        tp1_id = None
        if tp1 > 0 and tp1_pct > 0:
            tp1_qty = round_qty(symbol, qty * (tp1_pct / 100))
            if tp1_qty > 0:
                try:
                    t1 = client.futures_create_order(
                        symbol=symbol,
                        side=exit_side,
                        type="TAKE_PROFIT_MARKET",
                        stopPrice=round_price(symbol, tp1),
                        quantity=tp1_qty,
                        reduceOnly=True,
                        workingType="MARK_PRICE"
                    )
                    tp1_id = t1['orderId']
                except Exception as e:
                    print(f"TP1 Terminal Error: {e}")

        # 5. ATTACH TP2 (REMAINDER)
        tp2_id = None
        if tp2 > 0:
            try:
                # Use closePosition=True for TP2 to sweep any remaining dust
                t2 = client.futures_create_order(
                    symbol=symbol,
                    side=exit_side,
                    type="TAKE_PROFIT_MARKET",
                    stopPrice=round_price(symbol, tp2),
                    closePosition=True,
                    workingType="MARK_PRICE"
                )
                tp2_id = t2['orderId']
            except Exception as e:
                print(f"TP2 Terminal Error: {e}")

        update_trade_stats(symbol)
        return {
            "success": True, 
            "message": f"✅ ALL SET: Entry @ {actual_entry}, SL @ {sl_price} and TPs placed."
        }

    except Exception as e:
        print(f"CRITICAL ERROR: {str(e)}")
        return {"success": False, "message": f"❌ Execution Failed: {str(e)}"}
def partial_close_position(symbol, close_percent=None, close_qty=None):
    try:
        client = get_client()
        if client is None:
            return {"success": False, "message": "❌ Binance client not connected"}
        
        positions = client.futures_position_information(symbol=symbol, recvWindow=10000)
        position = None
        for pos in positions:
            if abs(float(pos['positionAmt'])) > 0:
                position = pos
                break
        
        if not position:
            return {"success": False, "message": f"❌ No open position for {symbol}"}
        
        position_amt = float(position['positionAmt'])
        
        if close_qty:
            qty_to_close = abs(close_qty)
        elif close_percent:
            qty_to_close = abs(position_amt) * (close_percent / 100)
        else:
            return {"success": False, "message": "❌ Must specify close_percent or close_qty"}
        
        qty_to_close = round_qty(symbol, qty_to_close)
        close_side = Client.SIDE_SELL if position_amt > 0 else Client.SIDE_BUY
        
        print(f"\n📉 Partial close: {qty_to_close} {symbol}")
        order = client.futures_create_order(
            symbol=symbol,
            side=close_side,
            type="MARKET",
            quantity=qty_to_close,
            recvWindow=10000
        )
        
        print(f"✅ Partial close executed: Order ID {order['orderId']}")
        return {
            "success": True,
            "message": f"✅ Partially closed {qty_to_close} {symbol}",
            "orderId": order['orderId']
        }
        
    except Exception as e:
        print(f"❌ Partial close error: {e}")
        traceback.print_exc()
        return {"success": False, "message": f"❌ Error: {str(e)}"}

def close_position(symbol):
    try:
        client = get_client()
        if client is None:
            return {"success": False, "message": "❌ Binance client not connected"}
        
        positions = client.futures_position_information(symbol=symbol, recvWindow=10000)
        position = None
        for pos in positions:
            if abs(float(pos['positionAmt'])) > 0:
                position = pos
                break
        
        if not position:
            return {"success": False, "message": f"❌ No open position for {symbol}"}
        
        position_amt = float(position['positionAmt'])
        close_side = Client.SIDE_SELL if position_amt > 0 else Client.SIDE_BUY
        
        print(f"\n🛑 Closing position: {symbol}")
        order = client.futures_create_order(
            symbol=symbol,
            side=close_side,
            type="MARKET",
            quantity=abs(position_amt),
            recvWindow=10000
        )
        
        try:
            client.futures_cancel_all_open_orders(symbol=symbol, recvWindow=10000)
            print(f"✅ Cancelled all open orders for {symbol}")
        except:
            pass
        
        print(f"✅ Position closed: Order ID {order['orderId']}")
        return {
            "success": True,
            "message": f"✅ Position closed for {symbol}",
            "orderId": order['orderId']
        }
        
    except Exception as e:
        print(f"❌ Close position error: {e}")
        traceback.print_exc()
        return {"success": False, "message": f"❌ Error: {str(e)}"}

def update_stop_loss(symbol, new_sl_percent):
    try:
        if new_sl_percent < config.SL_EDIT_MIN_PERCENT or new_sl_percent > config.SL_EDIT_MAX_PERCENT:
            return {
                "success": False, 
                "message": f"❌ SL adjustment must be between {config.SL_EDIT_MIN_PERCENT}% and {config.SL_EDIT_MAX_PERCENT}%"
            }
        
        client = get_client()
        if client is None:
            return {"success": False, "message": "❌ Binance client not connected"}
        
        positions = client.futures_position_information(symbol=symbol, recvWindow=10000)
        position = None
        for pos in positions:
            if abs(float(pos['positionAmt'])) > 0:
                position = pos
                break
        
        if not position:
            return {"success": False, "message": f"❌ No open position for {symbol}"}
        
        position_amt = float(position['positionAmt'])
        entry_price = float(position['entryPrice'])
        
        if position_amt > 0:
            new_sl_price = entry_price * (1 + new_sl_percent / 100)
        else:
            new_sl_price = entry_price * (1 - new_sl_percent / 100)
        
        new_sl_price = round_price(symbol, new_sl_price)
        
        open_orders = client.futures_get_open_orders(symbol=symbol, recvWindow=10000)
        for order in open_orders:
            if order['type'] in ['STOP_MARKET', 'STOP']:
                client.futures_cancel_order(symbol=symbol, orderId=order['orderId'], recvWindow=10000)
                print(f"✅ Cancelled old SL order: {order['orderId']}")
        
        exit_side = Client.SIDE_SELL if position_amt > 0 else Client.SIDE_BUY
        
        sl_order = client.futures_create_order(
            symbol=symbol,
            side=exit_side,
            type="STOP_MARKET",
            stopPrice=new_sl_price,
            closePosition=True,
            workingType="MARK_PRICE",
            recvWindow=10000
        )
        
        print(f"✅ New SL placed @ {new_sl_price}: Order ID {sl_order['orderId']}")
        return {
            "success": True,
            "message": f"✅ SL updated to {new_sl_price} ({new_sl_percent:+.2f}%)",
            "new_sl_price": new_sl_price,
            "orderId": sl_order['orderId']
        }
        
    except Exception as e:
        print(f"❌ Update SL error: {e}")
        traceback.print_exc()
        return {"success": False, "message": f"❌ Error: {str(e)}"}

def get_trade_history():
    try:
        client = get_client()
        if client is None:
            return []
        
        trades = client.futures_account_trades(limit=500, recvWindow=10000)
        
        trade_list = []
        for trade in trades:
            trade_list.append({
                'time': datetime.fromtimestamp(trade['time'] / 1000).strftime("%Y-%m-%d %H:%M:%S"),
                'symbol': trade['symbol'],
                'side': 'LONG' if trade['side'] == 'BUY' else 'SHORT',
                'qty': float(trade['qty']),
                'price': float(trade['price']),
                'realized_pnl': float(trade['realizedPnl']),
                'commission': float(trade['commission']),
                'order_id': trade['orderId']
            })
        
        trade_list.sort(key=lambda x: x['time'], reverse=True)
        return trade_list
        
    except Exception as e:
        print(f"Error getting trade history: {e}")
        traceback.print_exc()
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