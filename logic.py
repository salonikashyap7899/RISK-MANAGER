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
CACHE_DURATION = 5  # Cache duration in seconds

def sync_time_with_binance():
    """Sync local time with Binance server time"""
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
    """Get or create Binance client with error handling"""
    global _client
    if _client is None:
        try:
            time_offset = sync_time_with_binance()
            _client = Client(
                config.BINANCE_KEY, 
                config.BINANCE_SECRET,
                {'timeout': 20}
            )
            _client.timestamp_offset = time_offset
        except Exception as e:
            print(f"❌ Client initialization failed: {e}")
    return _client

def get_live_price(symbol):
    """Get current price for a symbol with caching"""
    now = time.time()
    if symbol in _price_cache and now - _price_cache_time.get(symbol, 0) < CACHE_DURATION:
        return _price_cache[symbol]
    
    try:
        client = get_client()
        if not client: return None
        ticker = client.futures_symbol_ticker(symbol=symbol)
        price = float(ticker['price'])
        _price_cache[symbol] = price
        _price_cache_time[symbol] = now
        return price
    except Exception as e:
        print(f"Error getting price for {symbol}: {e}")
        return None

def get_open_positions():
    """Get actual open positions from Binance"""
    try:
        client = get_client()
        if not client: return []
        
        acc_info = client.futures_account(recvWindow=10000)
        positions = []
        
        for pos in acc_info.get('positions', []):
            amt = float(pos.get('positionAmt', 0))
            if amt != 0:
                # Get entry price and unrealized profit
                positions.append({
                    'symbol': pos['symbol'],
                    'amount': amt,
                    'entry_price': float(pos['entryPrice']),
                    'unrealized_pnl': float(pos['unrealizedProfit']),
                    'leverage': pos['leverage'],
                    'side': 'LONG' if amt > 0 else 'SHORT',
                    'margin_type': pos['marginType'].upper()
                })
        return positions
    except Exception as e:
        print(f"Error getting positions: {e}")
        return []

def execute_trade_action(balance, symbol, side, entry, order_type, sl_type, sl_val, sizing, user_units, user_lev, margin_mode, tp1, tp1_pct, tp2):
    """Execute trade and set SL/TP orders"""
    try:
        client = get_client()
        if not client:
            return {"success": False, "message": "API Client Error"}

        # 1. Set Margin Mode & Leverage
        try:
            client.futures_change_margin_type(symbol=symbol, marginType=margin_mode.upper())
        except: pass # Ignore if already set
        
        client.futures_change_leverage(symbol=symbol, leverage=int(user_lev))

        # 2. Execute Main Order
        order_side = Client.SIDE_BUY if side.upper() == "LONG" else Client.SIDE_SELL
        
        if order_type == "MARKET":
            main_order = client.futures_create_order(
                symbol=symbol,
                side=order_side,
                type=Client.FUTURE_ORDER_TYPE_MARKET,
                quantity=user_units
            )
        else:
            main_order = client.futures_create_order(
                symbol=symbol,
                side=order_side,
                type=Client.FUTURE_ORDER_TYPE_LIMIT,
                timeInForce=Client.TIME_IN_FORCE_GTC,
                quantity=user_units,
                price=str(entry)
            )

        # 3. Small delay to ensure order is processed before placing TP/SL
        time.sleep(1)

        # 4. Execute Stop Loss
        sl_side = Client.SIDE_SELL if side.upper() == "LONG" else Client.SIDE_BUY
        
        # Calculate SL Price
        if sl_type == "PRICE":
            sl_price = sl_val
        else: # PERCENT
            sl_price = entry * (1 - sl_val/100) if side == "LONG" else entry * (1 + sl_val/100)

        # Get precision for the symbol to avoid Binance errors
        sl_price_str = "{:0.4f}".format(sl_price) # Basic rounding

        client.futures_create_order(
            symbol=symbol,
            side=sl_side,
            type=Client.FUTURE_ORDER_TYPE_STOP_MARKET,
            stopPrice=sl_price_str,
            closePosition=True,
            timeInForce=Client.TIME_IN_FORCE_GTC
        )

        # 5. Execute Take Profit if provided
        if tp1 > 0:
            client.futures_create_order(
                symbol=symbol,
                side=sl_side,
                type=Client.FUTURE_ORDER_TYPE_TAKE_PROFIT_MARKET,
                stopPrice="{:0.4f}".format(tp1),
                closePosition=True,
                timeInForce=Client.TIME_IN_FORCE_GTC
            )

        return {"success": True, "message": f"Successfully placed {side} on {symbol}"}

    except Exception as e:
        return {"success": False, "message": str(e)}

def get_trade_history():
    """Fetch trade history and ensure it includes the latest fills"""
    try:
        client = get_client()
        if not client: return []
        
        # Get all recent fills
        trades = client.futures_account_trades(limit=50, recvWindow=10000)
        
        formatted_trades = []
        for t in trades:
            formatted_trades.append({
                'time': datetime.fromtimestamp(t['time'] / 1000).strftime("%H:%M:%S"),
                'symbol': t['symbol'],
                'side': t['side'],
                'qty': t['qty'],
                'price': t['price'],
                'pnl': t['realizedPnl']
            })
        
        # Reverse to show newest at top
        return formatted_trades[::-1]
    except Exception as e:
        print(f"Error in history: {e}")
        return []

def get_today_stats():
    """Stub for stats calculation"""
    return {"total_trades": 0, "max_trades": config.MAX_TRADES_PER_DAY}

def calculate_position_sizing(unutilized, entry, sl_type, sl_val):
    """Calculates suggested units based on 1% risk"""
    if not entry or not sl_val:
        return {"error": "Missing entry or SL"}
    
    risk_amt = unutilized # 1% of total balance passed from app.py
    
    if sl_type == "PERCENT":
        sl_dist = (sl_val / 100) * entry
    else:
        sl_dist = abs(entry - sl_val)
        
    if sl_dist == 0: return {"error": "SL too close"}
    
    units = risk_amt / sl_dist
    return {"units": round(units, 3), "risk_amount": round(risk_amt, 2)}