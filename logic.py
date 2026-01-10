from flask import session
from datetime import datetime, date
from binance.client import Client
from binance.exceptions import BinanceAPIException
import config
import math
import traceback
import time

_client = None
_symbol_cache = None
_symbol_cache_time = 0
_price_cache = {}
_price_cache_time = {}
CACHE_DURATION = 5 

def sync_time_with_binance():
    try:
        import requests
        response = requests.get('https://fapi.binance.com/fapi/v1/time')
        server_time = response.json()['serverTime']
        local_time = int(time.time() * 1000)
        time_offset = server_time - local_time
        return time_offset
    except Exception as e:
        print(f"⚠️ Could not sync time: {e}")
        return 0

def get_client():
    global _client
    if _client is None:
        try:
            time_offset = sync_time_with_binance()
            _client = Client(config.BINANCE_KEY, config.BINANCE_SECRET, {'timeout': 20})
            _client.timestamp_offset = time_offset
        except Exception as e:
            print(f"❌ Client Init Error: {e}")
    return _client

def get_symbol_precision(symbol):
    """Fetches correct rounding precision for price and quantity"""
    try:
        client = get_client()
        info = client.futures_exchange_info()
        for s in info['symbols']:
            if s['symbol'] == symbol:
                # Precision for quantity
                qty_precision = 0
                for f in s['filters']:
                    if f['filterType'] == 'LOT_SIZE':
                        step_size = float(f['stepSize'])
                        qty_precision = int(round(-math.log(step_size, 10), 0))
                
                # Precision for price
                price_precision = 0
                for f in s['filters']:
                    if f['filterType'] == 'PRICE_FILTER':
                        tick_size = float(f['tickSize'])
                        price_precision = int(round(-math.log(tick_size, 10), 0))
                
                return qty_precision, price_precision
        return 3, 2 # Defaults
    except:
        return 3, 2

def get_live_price(symbol):
    client = get_client()
    try:
        ticker = client.futures_symbol_ticker(symbol=symbol)
        return float(ticker['price'])
    except:
        return 0

def calculate_position_sizing(balance, entry, sl_type, sl_val):
    if not entry or entry <= 0: return {"error": "Invalid entry price"}
    
    risk_amount = balance * (config.MAX_RISK_PERCENT / 100)
    
    if sl_type == "PRICE":
        sl_dist = abs(entry - sl_val)
    else:
        sl_dist = entry * (sl_val / 100)
        
    if sl_dist <= 0: return {"error": "Invalid SL distance"}
    
    qty = risk_amount / sl_dist
    lev = (qty * entry) / balance
    
    return {
        "qty": qty,
        "leverage": max(1, math.ceil(lev)),
        "risk": risk_amount,
        "sl_dist": sl_dist
    }

def execute_trade_action(balance, symbol, side, entry, order_type, sl_type, sl_val, sizing, user_qty=0, user_lev=0, margin_mode="ISOLATED", tp1=0, tp1_pct=0, tp2=0):
    try:
        client = get_client()
        qty_prec, price_prec = get_symbol_precision(symbol)
        
        # Determine Quantity and Leverage
        final_qty = user_qty if user_qty > 0 else sizing['qty']
        final_lev = int(user_lev if user_lev > 0 else sizing['leverage'])
        
        # Round quantities and prices for Binance
        final_qty = round(final_qty, qty_prec)
        entry = round(entry, price_prec)
        
        # Set Margin and Leverage FIRST
        try:
            client.futures_change_margin_type(symbol=symbol, marginType=margin_mode)
        except: pass # Ignore if already set
        client.futures_change_leverage(symbol=symbol, leverage=final_lev)

        # Place Main Order
        order_side = Client.SIDE_BUY if side == "LONG" else Client.SIDE_SELL
        params = {
            "symbol": symbol,
            "side": order_side,
            "type": order_type,
            "quantity": final_qty
        }
        if order_type == "LIMIT":
            params["price"] = entry
            params["timeInForce"] = "GTC"
            
        main_order = client.futures_create_order(**params)

        # Stop Loss
        sl_price = sl_val if sl_type == "PRICE" else (entry * (1 - sl_val/100) if side == "LONG" else entry * (1 + sl_val/100))
        sl_price = round(sl_price, price_prec)
        
        client.futures_create_order(
            symbol=symbol,
            side=Client.SIDE_SELL if side == "LONG" else Client.SIDE_BUY,
            type="STOP_MARKET",
            stopPrice=sl_price,
            closePosition=True
        )

        return {"success": True, "message": f"✅ {side} Order Placed! Qty: {final_qty}"}

    except Exception as e:
        traceback.print_exc()
        return {"success": False, "message": f"❌ Error: {str(e)}"}

def get_open_positions():
    try:
        client = get_client()
        acc = client.futures_account()
        pos = []
        for p in acc['positions']:
            amt = float(p['positionAmt'])
            if amt != 0:
                pos.append({
                    'symbol': p['symbol'],
                    'qty': abs(amt),
                    'side': 'LONG' if amt > 0 else 'SHORT',
                    'entry': float(p['entryPrice']),
                    'pnl': float(p['unrealizedProfit']),
                    'lev': p['leverage']
                })
        return pos
    except:
        return []

def get_trade_history():
    try:
        client = get_client()
        trades = client.futures_account_trades(limit=20)
        return [{
            'time': datetime.fromtimestamp(t['time']/1000).strftime("%H:%M:%S"),
            'symbol': t['symbol'],
            'side': 'BUY' if t['side'] == 'BUY' else 'SELL',
            'qty': t['qty'],
            'price': t['price'],
            'realized_pnl': t['realizedPnl']
        } for t in trades]
    except:
        return []

def get_today_stats():
    return {"total_trades": 0, "max_trades": config.MAX_TRADES_PER_DAY}