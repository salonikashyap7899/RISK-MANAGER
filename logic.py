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

def get_asset_rules(symbol):
    """Fetches exact Price and Quantity precision for a specific symbol from Binance."""
    try:
        info = client.futures_exchange_info()
        for s in info['symbols']:
            if s['symbol'] == symbol:
                # 1. Price Precision
                price_precision = int(s['pricePrecision'])
                
                # 2. Quantity (LOT_SIZE) Precision
                qty_precision = 0
                for f in s['filters']:
                    if f['filterType'] == 'LOT_SIZE':
                        step_size = f['stepSize'] # e.g., '0.100'
                        if '.' in step_size:
                            qty_precision = len(step_size.split('.')[1].rstrip('0'))
                        break
                return price_precision, qty_precision
        return 2, 3 # Safe defaults
    except:
        return 2, 3

def get_live_balance():
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
    try:
        if sl_value <= 0: return {"error": "SL Required"}
        risk_amount = unutilized_margin * 0.01
        sl_pct = (abs(sl_value - float(entry)) / float(entry) * 100) if sl_type == "SL Points" else sl_value
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

def execute_trade_action(balance, symbol, side, entry, order_type, sl_type, sl_val, sizing, u_units, u_lev, margin_mode, tp1, tp1_pct, tp2):
    today = datetime.utcnow().date().isoformat()
    day_stats = session["stats"].get(today, {"total": 0, "symbols": {}})
    
    if day_stats["total"] >= 4: return {"success": False, "message": "Global limit (4) reached."}
    if day_stats["symbols"].get(symbol, 0) >= 2: return {"success": False, "message": f"{symbol} limit (2) reached."}

    try:
        # Get Precision Rules
        p_prec, q_prec = get_asset_rules(symbol)

        # Set Leverage and Margin Mode
        lev = int(u_lev if u_lev > 0 else sizing["suggested_leverage"])
        client.futures_change_leverage(symbol=symbol, leverage=lev)
        try: client.futures_change_margin_type(symbol=symbol, marginType=margin_mode.upper())
        except: pass

        # ROUND EVERYTHING TO PREVENT APIERROR -1111
        units = round(u_units if u_units > 0 else sizing["suggested_units"], q_prec)
        entry_price = round(float(entry), p_prec)
        sl_price = round(float(sl_val), p_prec)
        
        b_side = Client.SIDE_BUY if side == "LONG" else Client.SIDE_SELL
        exit_side = Client.SIDE_SELL if side == "LONG" else Client.SIDE_BUY

        # 1. Place Main Order
        if order_type == "MARKET":
            client.futures_create_order(symbol=symbol, side=b_side, type='MARKET', quantity=abs(units))
        else:
            client.futures_create_order(symbol=symbol, side=b_side, type='LIMIT', timeInForce='GTC', quantity=abs(units), price=str(entry_price))

        # 2. LOG TO UI IMMEDIATELY (Prevents missing trade in log)
        day_stats["total"] += 1
        day_stats["symbols"][symbol] = day_stats["symbols"].get(symbol, 0) + 1
        session["stats"][today] = day_stats
        session["trades"].append({
            "timestamp": datetime.utcnow().isoformat(), 
            "symbol": symbol, 
            "side": side, 
            "entry_price": entry_price, 
            "units": units
        })
        session.modified = True

        # 3. Place Stop Loss (STOP_MARKET is the correct trigger type)
        if sl_price > 0:
            client.futures_create_order(
                symbol=symbol, side=exit_side, type='STOP_MARKET', 
                stopPrice=str(sl_price), closePosition=True
            )

        # 4. Place Take Profits
        if tp1 > 0:
            tp1_p = round(float(tp1), p_prec)
            tp1_q = round(abs(units) * (tp1_pct / 100), q_prec)
            if tp1_q > 0:
                client.futures_create_order(symbol=symbol, side=exit_side, type='TAKE_PROFIT_MARKET', stopPrice=str(tp1_p), quantity=tp1_q)

        if tp2 > 0:
            tp2_p = round(float(tp2), p_prec)
            tp2_q = round(abs(units) - round(abs(units) * (tp1_pct / 100), q_prec), q_prec)
            if tp2_q > 0:
                client.futures_create_order(symbol=symbol, side=exit_side, type='TAKE_PROFIT_MARKET', stopPrice=str(tp2_p), quantity=tp2_q)

        return {"success": True, "message": f"SUCCESS: {side} {symbol} placed with SL/TP."}
    except Exception as e:
        return {"success": False, "message": f"Execution Error: {str(e)}"}

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