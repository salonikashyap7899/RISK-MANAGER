# logic.py
from flask import session
from datetime import datetime
from math import ceil
from binance.client import Client
import config 

client = Client(config.BINANCE_KEY, config.BINANCE_SECRET)

def initialize_session():
    if "trades" not in session: session["trades"] = []
    if "stats" not in session: session["stats"] = {}

def get_all_exchange_symbols():
    try:
        info = client.futures_exchange_info()
        return sorted([s['symbol'] for s in info['symbols'] if s['status'] == 'TRADING' and s['quoteAsset'] == 'USDT'])
    except:
        return ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

def get_live_balance():
    try:
        acc = client.futures_account()
        return float(acc.get('totalWalletBalance', 0)), float(acc.get('totalInitialMargin', 0))
    except:
        return None, None

def get_live_price(symbol):
    try:
        ticker = client.futures_symbol_ticker(symbol=symbol)
        return float(ticker['price'])
    except:
        return None

def calculate_position_sizing(unutilized_margin, entry, sl_type, sl_value):
    """Restored leverage formula: (Units * Entry) / Unutilized Capital."""
    try:
        if sl_value <= 0: return {"error": "SL Required"}
        entry = float(entry)
        risk_amount = unutilized_margin * 0.01 
        
        if sl_type == "SL Points":
            sl_dist = sl_value + 20
        else: # % Movement
            sl_dist = (sl_value + 0.2) / 100 * entry
            
        suggested_units = risk_amount / sl_dist if sl_dist > 0 else 0
        raw_lev = (suggested_units * entry) / unutilized_margin if unutilized_margin > 0 else 1
        suggested_lev = ceil(raw_lev * 2) / 2 
        
        return {
            "suggested_units": round(suggested_units, 3),
            "suggested_leverage": max(1.0, suggested_lev),
            "risk_amount": round(risk_amount, 2),
            "error": None
        }
    except:
        return {"error": "Invalid Input"}

def execute_trade_action(balance, symbol, side, entry, order_type, sl_type, sl_value, sizing, u_units, u_lev, margin_mode, tp1, tp1_pct, tp2):
    today = datetime.utcnow().date().isoformat()
    day_stats = session["stats"].get(today, {"total": 0, "symbols": {}})
    
    if day_stats["total"] >= 4:
        return {"success": False, "message": "REJECTED: Daily limit of 4 trades reached."}
    if day_stats["symbols"].get(symbol, 0) >= 2:
        return {"success": False, "message": f"REJECTED: Limit (2/day) reached for {symbol}."}

    try:
        units = u_units if u_units > 0 else sizing["suggested_units"]
        lev = int(u_lev if u_lev > 0 else sizing["suggested_leverage"])

        try: client.futures_change_margin_type(symbol=symbol, marginType=margin_mode.upper())
        except: pass 
        client.futures_change_leverage(symbol=symbol, leverage=lev)
        
        b_side = Client.SIDE_BUY if side == "LONG" else Client.SIDE_SELL
        tp_side = Client.SIDE_SELL if side == "LONG" else Client.SIDE_BUY
        
        # 1. Place Main Order
        if order_type == "MARKET":
            client.futures_create_order(symbol=symbol, side=b_side, type='MARKET', quantity=abs(round(units, 3)))
        else:
            client.futures_create_order(symbol=symbol, side=b_side, type='LIMIT', timeInForce='GTC', quantity=abs(round(units, 3)), price=str(entry))

        # 2. FIXED: Added Stop Loss (SL) order to Binance
        sl_price = entry - sl_value if side == "LONG" else entry + sl_value
        if sl_type == "SL % Movement":
            sl_price = entry * (1 - (sl_value/100)) if side == "LONG" else entry * (1 + (sl_value/100))
        client.futures_create_order(symbol=symbol, side=tp_side, type='STOP_MARKET', stopPrice=round(sl_price, 2), closePosition=True)

        # 3. Take Profit Orders
        if tp1 > 0:
            q1 = abs(round(units * (tp1_pct / 100), 3))
            client.futures_create_order(symbol=symbol, side=tp_side, type='LIMIT', timeInForce='GTC', quantity=q1, price=str(tp1))
        if tp2 > 0:
            q2 = abs(round(units - (units * (tp1_pct / 100)), 3))
            client.futures_create_order(symbol=symbol, side=tp_side, type='LIMIT', timeInForce='GTC', quantity=q2, price=str(tp2))

        # Update Session
        day_stats["total"] += 1
        day_stats["symbols"][symbol] = day_stats["symbols"].get(symbol, 0) + 1
        session["stats"][today] = day_stats
        session["trades"].append({"timestamp": datetime.utcnow().isoformat(), "symbol": symbol, "side": side, "entry_price": entry, "units": units})
        session.modified = True
        return {"success": True, "message": f"SUCCESS: {side} {symbol} placed with SL/TP."}
    except Exception as e:
        return {"success": False, "message": str(e)}