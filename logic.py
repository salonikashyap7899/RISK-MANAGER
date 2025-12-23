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
    if "capital" not in session: session["capital"] = config.TOTAL_CAPITAL_DEFAULT

def get_live_balance():
    try:
        acc = client.futures_account()
        return float(acc.get('totalWalletBalance', 0)), float(acc.get('totalInitialMargin', 0))
    except Exception as e:
        print(f"Connection Error: {e}")
        return None, None

def get_live_price(symbol):
    """Fetches real-time price from Binance Futures exchange."""
    try:
        b_symbol = symbol.replace("USD", "USDT")
        ticker = client.futures_symbol_ticker(symbol=b_symbol)
        return float(ticker['price'])
    except:
        return None

def calculate_position_sizing(balance, entry, sl_type, sl_value):
    try:
        entry = float(entry)
        sl_value = float(sl_value)
        risk_amount = balance * (config.RISK_PERCENT / 100.0)
        if sl_type == "SL Points":
            sl_distance = sl_value + 20 
            suggested_units = risk_amount / sl_distance if sl_distance > 0 else 0
        else:
            sl_distance = sl_value + 0.2 
            suggested_units = risk_amount / (sl_distance / 100 * entry) if sl_distance > 0 else 0
        suggested_lev = (suggested_units * entry) / balance if balance > 0 else 0
        return {
            "suggested_units": round(suggested_units, 4),
            "suggested_leverage": ceil(suggested_lev * 2) / 2,
            "risk_amount": round(risk_amount, 2),
            "max_leverage_info": f"{ceil(suggested_lev * 2) / 2:.1f}x",
            "error": None if sl_value > 0 else "SL Required"
        }
    except:
        return {"error": "Invalid Input"}

def execute_trade_action(balance, symbol, side, entry, sl_type, sl_value, sizing, user_units, user_lev, margin_mode, tp1, tp2):
    try:
        b_symbol = symbol.replace("USD", "USDT")
        units = float(user_units) if float(user_units) > 0 else sizing["suggested_units"]
        lev = int(user_lev) if int(user_lev) > 0 else int(sizing["suggested_leverage"])

        # 1. Set Margin Type and Leverage
        try:
            client.futures_change_margin_type(symbol=b_symbol, marginType=margin_mode.upper())
        except: pass 
        client.futures_change_leverage(symbol=b_symbol, leverage=lev)
        
        # 2. Place Main Market Order
        b_side = Client.SIDE_BUY if side == "LONG" else Client.SIDE_SELL
        client.futures_create_order(symbol=b_symbol, side=b_side, type='MARKET', quantity=abs(round(units, 3)))
        
        # 3. Place TP Orders (Limit Orders)
        tp_side = Client.SIDE_SELL if side == "LONG" else Client.SIDE_BUY
        if tp1 > 0:
            client.futures_create_order(symbol=b_symbol, side=tp_side, type='LIMIT', timeInForce='GTC', quantity=abs(round(units/2, 3)), price=str(tp1))
        if tp2 > 0:
            client.futures_create_order(symbol=b_symbol, side=tp_side, type='LIMIT', timeInForce='GTC', quantity=abs(round(units/2, 3)), price=str(tp2))

        trade = {"timestamp": datetime.utcnow().isoformat(), "symbol": symbol, "side": side, "entry_price": entry, "units": units}
        session["trades"].append(trade)
        session.modified = True
        return {"success": True, "message": f"SUCCESS: {side} {symbol} Executed"}
    except Exception as e:
        return {"success": False, "message": f"API ERROR: {str(e)}"}