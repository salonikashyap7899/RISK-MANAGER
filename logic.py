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
    """Fetches exact Price and Quantity precision for a symbol."""
    try:
        info = client.futures_exchange_info()
        for s in info['symbols']:
            if s['symbol'] == symbol:
                p_prec = int(s['pricePrecision'])
                q_prec = 0
                for f in s['filters']:
                    if f['filterType'] == 'LOT_SIZE':
                        step = f['stepSize']
                        q_prec = len(step.split('.')[1].rstrip('0')) if '.' in step else 0
                return p_prec, q_prec
        return 2, 3
    except: return 2, 3

def get_live_balance():
    try:
        acc = client.futures_account()
        usdt = next((i for i in acc.get('assets', []) if i["asset"] == "USDT"), None)
        if usdt:
            return (float(usdt["availableBalance"]) + float(usdt["initialMargin"])), float(usdt["initialMargin"])
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
        return {"suggested_units": suggested_units, "suggested_leverage": int(max(1, suggested_lev)), "risk_amount": round(risk_amount, 2), "error": None}
    except: return {"error": "Math Error"}

def execute_trade_action(balance, symbol, side, entry, order_type, sl_type, sl_val, sizing, u_units, u_lev, margin_mode, tp1, tp1_pct, tp2):
    today = datetime.utcnow().date().isoformat()
    day_stats = session["stats"].get(today, {"total": 0, "symbols": {}})
    if day_stats["total"] >= 4 or day_stats["symbols"].get(symbol, 0) >= 2:
        return {"success": False, "message": "Limit Reached"}

    try:
        p_prec, q_prec = get_asset_rules(symbol)
        lev = int(u_lev if u_lev > 0 else sizing["suggested_leverage"])
        client.futures_change_leverage(symbol=symbol, leverage=lev)
        try: client.futures_change_margin_type(symbol=symbol, marginType=margin_mode.upper())
        except: pass

        # ROUNDING EVERYTHING TO PREVENT PRECISION ERROR -1111
        units = round(u_units if u_units > 0 else sizing["suggested_units"], q_prec)
        rounded_entry = round(float(entry), p_prec)
        b_side = Client.SIDE_BUY if side == "LONG" else Client.SIDE_SELL
        exit_side = Client.SIDE_SELL if side == "LONG" else Client.SIDE_BUY

        # 1. Main Entry Order
        if order_type == "MARKET":
            client.futures_create_order(symbol=symbol, side=b_side, type='MARKET', quantity=abs(units))
        else:
            client.futures_create_order(symbol=symbol, side=b_side, type='LIMIT', timeInForce='GTC', quantity=abs(units), price=str(rounded_entry))

        # 2. Log to UI Session
        day_stats["total"] += 1
        day_stats["symbols"][symbol] = day_stats["symbols"].get(symbol, 0) + 1
        session["stats"][today] = day_stats
        session["trades"].append({"timestamp": datetime.utcnow().isoformat(), "symbol": symbol, "side": side, "entry_price": rounded_entry, "units": units})
        session.modified = True

        # 3. ALGO ORDERS for SL and TP (Fix for Error -4120)
        if sl_val > 0:
            client._post('fapi/v1/algoOrder', data={
                "symbol": symbol, "side": exit_side, "type": "STOP_MARKET", 
                "stopPrice": str(round(sl_val, p_prec)), "quantity": str(abs(units)), "reduceOnly": "true"
            })
        if tp1 > 0:
            tp1_q = round(abs(units) * (tp1_pct / 100), q_prec)
            client._post('fapi/v1/algoOrder', data={
                "symbol": symbol, "side": exit_side, "type": "TAKE_PROFIT_MARKET", 
                "stopPrice": str(round(tp1, p_prec)), "quantity": str(tp1_q), "reduceOnly": "true"
            })

        return {"success": True, "message": f"SUCCESS: {symbol} active with SL/TP."}
    except Exception as e:
        return {"success": False, "message": f"Execution Error: {str(e)}"}

def get_all_exchange_symbols():
    try:
        info = client.futures_exchange_info()
        return sorted([s['symbol'] for s in info['symbols'] if s['status'] == 'TRADING' and s['quoteAsset'] == 'USDT'])
    except: return ["BTCUSDT"]

def get_live_price(symbol):
    try:
        return float(client.futures_symbol_ticker(symbol=symbol)['price'])
    except: return None