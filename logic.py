# logic.py
from flask import session
from datetime import datetime
from math import ceil
from binance.client import Client
import config # logic.py
from flask import session
from datetime import datetime
from math import ceil
from binance.client import Client
import config 

def get_binance_client():
    """Initializes the Binance connection with optional Proxy support."""
    proxies = {
        'http': config.PROXY_URL,
        'https': config.PROXY_URL
    } if config.PROXY_URL else None
    return Client(config.BINANCE_KEY, config.BINANCE_SECRET, {"proxies": proxies})

def initialize_session():
    if "trades" not in session:
        session["trades"] = []
    if "stats" not in session:
        session["stats"] = {}
    if "capital" not in session:
        session["capital"] = config.TOTAL_CAPITAL_DEFAULT

def calculate_position_sizing(balance, entry, sl_type, sl_value):
    try:
        entry = float(entry)
        sl_value = float(sl_value)
    except:
        return {"error": "Invalid Entry or SL"}

    risk_amount = balance * (config.RISK_PERCENT / 100.0)

    # MAINTAINED: SL Points Formula (Risk / (SL + 20))
    if sl_type == "SL Points":
        if sl_value <= 0: return {"error": "SL Required"}
        sl_distance = sl_value + 20
        suggested_units = risk_amount / sl_distance
        suggested_lev = (suggested_units * entry) / balance if balance > 0 else 0
        suggested_lev = ceil(suggested_lev * 2) / 2 

        return {
            "suggested_units": suggested_units,
            "suggested_leverage": suggested_lev,
            "risk_amount": risk_amount,
            "max_leverage_info": f"{suggested_lev:.1f}x",
            "error": None,
            "sl_mode": sl_type
        }

    # MAINTAINED: SL % Formula (Risk / (SL% + 0.2%))
    elif sl_type == "SL % Movement":
        if sl_value <= 0: return {"error": "SL Required"}
        sl_distance = sl_value + 0.2
        suggested_units = risk_amount / (sl_distance / 100 * entry)
        suggested_lev = 100 / sl_value
        suggested_lev = ceil(suggested_lev * 2) / 2

        return {
            "suggested_units": suggested_units,
            "suggested_leverage": suggested_lev,
            "risk_amount": risk_amount,
            "max_leverage_info": f"{suggested_lev:.1f}x",
            "error": None,
            "sl_mode": sl_type
        }
    return {"error": "Invalid SL Type"}

def execute_trade_action(balance, symbol, side, entry, sl_type, sl_value, order_type, tp_list, sizing, user_units, user_lev):
    initialize_session()
    today = datetime.utcnow().date().isoformat()
    
    # 1. SL Mandatory Rule
    if float(sl_value) <= 0:
        return {"success": False, "message": "SL REQUIRED — Trade cannot be placed."}

    # 2. Daily Limit Checks (4 trades total, 2 per symbol)
    stats = session.get("stats", {}).get(today, {"total": 0, "by_symbol": {}})
    if stats["total"] >= config.DAILY_MAX_TRADES:
        return {"success": False, "message": "Daily limit reached."}
    
    if stats["by_symbol"].get(symbol, 0) >= config.DAILY_MAX_PER_SYMBOL:
        return {"success": False, "message": f"Max daily trades for {symbol} reached."}

    # 3. Execution logic via API
    try:
        client = get_binance_client()
        # ... logic to place futures order goes here ...
        # (This uses the Proxy from config.py automatically)
    except Exception as e:
        return {"success": False, "message": f"Binance Error: {str(e)}"}

    # 4. Log trade to session
    trade = {
        "timestamp": datetime.utcnow().isoformat(), "date": today, "symbol": symbol, "side": side,
        "entry_price": float(entry), "stop_loss": float(sl_value), "sl_mode": sl_type,
        "units": user_units if user_units > 0 else sizing["suggested_units"],
        "leverage": user_lev if user_lev > 0 else sizing["suggested_leverage"],
        "risk_usd": sizing["risk_amount"], "status": "open"
    }
    session["trades"].append(trade)
    # Update Stats
    if today not in session["stats"]: session["stats"][today] = {"total": 0, "by_symbol": {}}
    session["stats"][today]["total"] += 1
    session["stats"][today]["by_symbol"][symbol] = session["stats"][today]["by_symbol"].get(symbol, 0) + 1
    session.modified = True

    return {"success": True, "message": "Order Executed Successfully", "trade": trade}

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