# logic.py
from flask import session
from datetime import datetime
from math import ceil
from binance.client import Client
import config 

# TO RESOLVE RESTRICTED LOCATION ERROR:
# You must use a proxy if running on Render. Example:
# client = Client(config.BINANCE_KEY, config.BINANCE_SECRET, {"proxies": {"https": "http://user:pass@host:port"}})
client = Client(config.BINANCE_KEY, config.BINANCE_SECRET)

def initialize_session():
    if "trades" not in session: session["trades"] = []
    if "stats" not in session: session["stats"] = {}
    if "capital" not in session: session["capital"] = config.TOTAL_CAPITAL_DEFAULT

def get_live_balance():
    """Fetches real account balance from Binance Futures."""
    try:
        acc = client.futures_account()
        return float(acc.get('totalWalletBalance', 0)), float(acc.get('totalInitialMargin', 0))
    except Exception as e:
        print(f"Connection Error: {e}")
        return None, None

def calculate_position_sizing(balance, entry, sl_type, sl_value):
    try:
        entry = float(entry)
        sl_value = float(sl_value)
        risk_amount = balance * (config.RISK_PERCENT / 100.0) #

        if sl_type == "SL Points":
            if sl_value <= 0: return {"error": "SL Required"}
            sl_distance = sl_value + 20 # Your specific buffer
            suggested_units = risk_amount / sl_distance
            suggested_lev = (suggested_units * entry) / balance if balance > 0 else 0
            max_lev_info = f"{ceil(suggested_lev * 2) / 2:.1f}x"
        else: # SL % Movement
            if sl_value <= 0: return {"error": "SL Required"}
            sl_distance = sl_value + 0.2 # Your specific buffer
            suggested_units = risk_amount / (sl_distance / 100 * entry)
            suggested_lev = 100 / sl_value
            max_lev_info = f"{ceil(suggested_lev * 2) / 2:.1f}x"

        return {
            "suggested_units": round(suggested_units, 4),
            "suggested_leverage": ceil(suggested_lev * 2) / 2,
            "risk_amount": round(risk_amount, 2),
            "max_leverage_info": max_lev_info,
            "error": None
        }
    except:
        return {"error": "Invalid Input"}

def execute_trade_action(balance, symbol, side, entry, sl_type, sl_value, sizing, user_units, user_lev):
    today = datetime.utcnow().date().isoformat()
    # Check Limits
    stats = session.get("stats", {}).get(today, {"total": 0, "by_symbol": {}})
    if stats["total"] >= config.DAILY_MAX_TRADES:
        return {"success": False, "message": "Daily trade limit reached."}

    try:
        b_symbol = symbol.replace("USD", "USDT")
        units = user_units if user_units > 0 else sizing["suggested_units"]
        lev = user_lev if user_lev > 0 else sizing["suggested_leverage"]

        # Binance Orders
        client.futures_change_leverage(symbol=b_symbol, leverage=int(lev))
        b_side = Client.SIDE_BUY if side == "LONG" else Client.SIDE_SELL
        client.futures_create_order(symbol=b_symbol, side=b_side, type='MARKET', quantity=abs(round(units, 3)))
        
        # Log to Session
        trade = {"timestamp": datetime.utcnow().isoformat(), "date": today, "symbol": symbol, "side": side, 
                 "entry_price": entry, "stop_loss": sl_value, "sl_mode": sl_type, "units": units, 
                 "leverage": lev, "risk_usd": sizing["risk_amount"]}
        session["trades"].append(trade)
        session.modified = True
        return {"success": True, "message": f"SUCCESS: {side} {symbol} Executed"}
    except Exception as e:
        return {"success": False, "message": f"API ERROR: {str(e)}"}