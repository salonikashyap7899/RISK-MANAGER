# logic.py
from flask import session
from datetime import datetime
from math import ceil
from binance.client import Client
import config 

# Initialize Binance Client
client = Client(config.BINANCE_KEY, config.BINANCE_SECRET)

def initialize_session():
    if "trades" not in session: session["trades"] = []
    if "stats" not in session: session["stats"] = {}
    if "capital" not in session: session["capital"] = config.TOTAL_CAPITAL_DEFAULT

def get_all_exchange_symbols():
    """Fetches all active USDT futures symbols from the exchange."""
    try:
        info = client.futures_exchange_info()
        symbols = [s['symbol'] for s in info['symbols'] if s['status'] == 'TRADING' and s['quoteAsset'] == 'USDT']
        return sorted(symbols)
    except:
        return ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

def get_live_balance():
    try:
        acc = client.futures_account()
        return float(acc.get('totalWalletBalance', 0)), float(acc.get('totalInitialMargin', 0))
    except Exception as e:
        print(f"Connection Error: {e}")
        return None, None

def get_live_price(symbol):
    try:
        ticker = client.futures_symbol_ticker(symbol=symbol)
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

def execute_trade_action(balance, symbol, side, entry, order_type, sl_type, sl_value, sizing, u_units, u_lev, margin_mode, tp1, tp1_pct, tp2):
    try:
        units = u_units if u_units > 0 else sizing["suggested_units"]
        lev = int(u_lev if u_lev > 0 else sizing["suggested_leverage"])

        # 1. Set Margin Type & Leverage
        try:
            client.futures_change_margin_type(symbol=symbol, marginType=margin_mode.upper())
        except: pass 
        client.futures_change_leverage(symbol=symbol, leverage=lev)
        
        # 2. Entry Order
        b_side = Client.SIDE_BUY if side == "LONG" else Client.SIDE_SELL
        if order_type == "MARKET":
            client.futures_create_order(symbol=symbol, side=b_side, type='MARKET', quantity=abs(round(units, 3)))
        else: # LIMIT
            client.futures_create_order(symbol=symbol, side=b_side, type='LIMIT', timeInForce='GTC', quantity=abs(round(units, 3)), price=str(entry))

        # 3. Take Profit Scaling
        tp_side = Client.SIDE_SELL if side == "LONG" else Client.SIDE_BUY
        if tp1 > 0:
            qty1 = abs(round(units * (tp1_pct / 100), 3))
            client.futures_create_order(symbol=symbol, side=tp_side, type='LIMIT', timeInForce='GTC', quantity=qty1, price=str(tp1))
        if tp2 > 0:
            qty2 = abs(round(units - (units * (tp1_pct / 100)), 3))
            client.futures_create_order(symbol=symbol, side=tp_side, type='LIMIT', timeInForce='GTC', quantity=qty2, price=str(tp2))

        trade = {"timestamp": datetime.utcnow().isoformat(), "symbol": symbol, "side": side, "entry_price": entry, "units": units}
        session["trades"].append(trade)
        session.modified = True
        return {"success": True, "message": f"SUCCESS: {order_type} {side} {symbol}"}
    except Exception as e:
        return {"success": False, "message": f"API ERROR: {str(e)}"}