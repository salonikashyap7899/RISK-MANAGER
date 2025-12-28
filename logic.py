# logic.py
from flask import session
from datetime import datetime
from math import floor
from binance.client import Client
import config 

client = Client(config.BINANCE_KEY, config.BINANCE_SECRET)

def initialize_session():
    if "trades" not in session: session["trades"] = []
    if "stats" not in session: session["stats"] = {}

def get_symbol_precision(symbol):
    """Fetches correct decimal precision for the symbol quantity."""
    try:
        info = client.futures_exchange_info()
        for s in info['symbols']:
            if s['symbol'] == symbol:
                # Find the LOT_SIZE filter to get stepSize
                for f in s['filters']:
                    if f['filterType'] == 'LOT_SIZE':
                        step_size = f['stepSize']
                        return step_size.find('1') - step_size.find('.')
        return 3 # Default fallback
    except: return 3

def get_live_balance():
    """Fetches real-time available USDT balance."""
    try:
        acc = client.futures_account()
        usdt = next((i for i in acc.get('assets', []) if i["asset"] == "USDT"), None)
        if usdt:
            available = float(usdt.get('availableBalance', 0))
            margin = float(usdt.get('initialMargin', 0))
            return (available + margin), margin
        return None, None
    except: return None, None

def calculate_position_sizing(unutilized_margin, entry, sl_type, sl_value):
    """
    Max Leverage = 100 / (SL% + 0.2%)
    Pos Size = {Risk / (SL% + 0.2%)} * 100
    """
    try:
        if sl_value <= 0: return {"error": "SL Required"}
        risk_amount = unutilized_margin * 0.01
        sl_pct = (sl_value / float(entry) * 100) if sl_type == "SL Points" else sl_value
        movement = sl_pct + 0.2
        
        notional = (risk_amount / movement) * 100
        suggested_units = notional / float(entry)
        suggested_lev = min(100, floor(100 / movement))
        
        return {
            "suggested_units": suggested_units,
            "suggested_leverage": int(max(1, suggested_lev)),
            "risk_amount": round(risk_amount, 2),
            "error": None
        }
    except: return {"error": "Invalid Math"}

def execute_trade_action(balance, symbol, side, entry, order_type, sl_type, sl_value, sizing, u_units, u_lev, margin_mode, tp1, tp1_pct, tp2):
    today = datetime.utcnow().date().isoformat()
    day_stats = session["stats"].get(today, {"total": 0, "symbols": {}})
    
    # Limit Checks: 4 global, 2 per symbol
    if day_stats["total"] >= 4: return {"success": False, "message": "Global limit (4) reached."}
    if day_stats["symbols"].get(symbol, 0) >= 2: return {"success": False, "message": f"{symbol} limit (2) reached."}

    try:
        # Step 1: Fix One-Way Mode (Error -4061 fix)
        try: client.futures_change_position_mode(dualSidePosition=False)
        except: pass 
        
        # Step 2: Set leverage and margin
        lev = int(u_lev if u_lev > 0 else sizing["suggested_leverage"])
        client.futures_change_leverage(symbol=symbol, leverage=lev)
        try: client.futures_change_margin_type(symbol=symbol, marginType=margin_mode.upper())
        except: pass

        # Step 3: Handle Quantity Precision (Filter Failure fix)
        precision = get_symbol_precision(symbol)
        units = round(u_units if u_units > 0 else sizing["suggested_units"], precision)

        # Step 4: Place Main Order
        b_side = Client.SIDE_BUY if side == "LONG" else Client.SIDE_SELL
        if order_type == "MARKET":
            client.futures_create_order(symbol=symbol, side=b_side, type='MARKET', quantity=abs(units))
        else:
            client.futures_create_order(symbol=symbol, side=b_side, type='LIMIT', timeInForce='GTC', quantity=abs(units), price=str(entry))

        # Step 5: Calculate and Place Stop Loss Order
        if sl_value > 0:
            try:
                # Calculate SL price based on sl_type
                if sl_type == "SL Points":
                    # SL Points: absolute price difference
                    sl_price = entry - sl_value if side == "LONG" else entry + sl_value
                else:
                    # SL % Movement: percentage of entry price
                    sl_pct = sl_value / 100.0
                    sl_price = entry * (1 - sl_pct) if side == "LONG" else entry * (1 + sl_pct)
                
                # SL side is opposite to main order side
                sl_side = Client.SIDE_SELL if side == "LONG" else Client.SIDE_BUY
                # Place STOP_MARKET order for stop loss
                client.futures_create_order(
                    symbol=symbol,
                    side=sl_side,
                    type='STOP_MARKET',
                    stopPrice=str(round(sl_price, 8)),
                    closePosition=True
                )
            except Exception as sl_error:
                pass  # Continue even if SL fails

        # Step 6: Place Take Profit Orders (TP1 and TP2)
        if tp1 > 0:
            try:
                # Calculate TP1 quantity based on percentage
                tp1_qty = abs(units) * (tp1_pct / 100.0)
                tp1_qty = round(tp1_qty, precision)
                if tp1_qty > 0:
                    tp_side = Client.SIDE_SELL if side == "LONG" else Client.SIDE_BUY
                    client.futures_create_order(
                        symbol=symbol,
                        side=tp_side,
                        type='TAKE_PROFIT_MARKET',
                        stopPrice=str(round(tp1, 8)),
                        quantity=tp1_qty
                    )
            except Exception as tp1_error:
                pass  # Continue even if TP1 fails

        if tp2 > 0:
            try:
                # TP2 gets remaining quantity (100% - tp1_pct%)
                tp2_qty = abs(units) * ((100 - tp1_pct) / 100.0)
                tp2_qty = round(tp2_qty, precision)
                if tp2_qty > 0:
                    tp_side = Client.SIDE_SELL if side == "LONG" else Client.SIDE_BUY
                    client.futures_create_order(
                        symbol=symbol,
                        side=tp_side,
                        type='TAKE_PROFIT_MARKET',
                        stopPrice=str(round(tp2, 8)),
                        quantity=tp2_qty
                    )
            except Exception as tp2_error:
                pass  # Continue even if TP2 fails

        # Update stats
        day_stats["total"] += 1
        day_stats["symbols"][symbol] = day_stats["symbols"].get(symbol, 0) + 1
        session["stats"][today] = day_stats
        session["trades"].append({"timestamp": datetime.utcnow().isoformat(), "symbol": symbol, "side": side, "entry_price": entry, "units": units})
        session.modified = True
        return {"success": True, "message": f"SUCCESS: {side} {symbol} placed."}
    except Exception as e:
        return {"success": False, "message": str(e)}

def get_all_exchange_symbols():
    try:
        info = client.futures_exchange_info()
        return sorted([s['symbol'] for s in info['symbols'] if s['status'] == 'TRADING' and s['quoteAsset'] == 'USDT'])
    except: return ["BTCUSDT", "ETHUSDT"]

def get_live_price(symbol):
    try:
        ticker = client.futures_symbol_ticker(symbol=symbol)
        return float(ticker['price'])
    except: return None