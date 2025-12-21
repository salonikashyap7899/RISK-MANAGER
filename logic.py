# logic.py
from flask import session
from datetime import datetime
from math import ceil
from binance.client import Client
import config 

# Initialize Binance Client
# If on Render, you might need a proxy due to US restrictions.
client = Client(config.BINANCE_KEY, config.BINANCE_SECRET)

def initialize_session():
    if "trades" not in session:
        session["trades"] = []
    if "stats" not in session:
        session["stats"] = {}
    if "capital" not in session:
        session["capital"] = config.TOTAL_CAPITAL_DEFAULT

def get_live_balance():
    """Fetches real account balance from Binance Futures."""
    try:
        acc = client.futures_account()
        return float(acc.get('totalWalletBalance', 0)), float(acc.get('totalInitialMargin', 0))
    except:
        return None, None

def _today_iso():
    return datetime.utcnow().date().isoformat()

def calculate_position_sizing(balance, entry, sl_type, sl_value):
    try:
        entry = float(entry)
        sl_value = float(sl_value)
    except:
        return {"error": "Invalid Entry or SL"}

    risk_amount = balance * (config.RISK_PERCENT / 100.0)

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

    elif sl_type == "SL % Movement":
        if sl_value <= 0: return {"error": "SL Required"}
        sl_distance_pct = sl_value + 0.2
        # Math matching your HTML Formula: Risk / (SL% / 100)
        notional = risk_amount / (sl_distance_pct / 100)
        suggested_units = notional / entry if entry > 0 else 0
        suggested_lev = 100 / sl_distance_pct
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
    if float(sl_value) <= 0:
        return {"success": False, "message": "SL REQUIRED — Trade cannot be placed."}

    # Daily Limits
    today = _today_iso()
    trades = session["trades"]
    todays = [t for t in trades if t["date"] == today]
    if len(todays) >= config.DAILY_MAX_TRADES:
        return {"success": False, "message": f"Daily {config.DAILY_MAX_TRADES} trades limit reached."}

    symbol_trades = [t for t in todays if t["symbol"] == symbol]
    if len(symbol_trades) >= config.DAILY_MAX_PER_SYMBOL:
        return {"success": False, "message": f"Max {config.DAILY_MAX_PER_SYMBOL} daily trades allowed for {symbol}."}

    units_to_use = user_units if user_units > 0 else sizing["suggested_units"]
    lev_to_use = user_lev if user_lev > 0 else sizing["suggested_leverage"]

    if user_units > sizing["suggested_units"]:
        return {"success": False, "message": f"Lot Size cannot exceed suggested: {sizing['suggested_units']:.4f}"}
    if user_lev > sizing["suggested_leverage"]:
        return {"success": False, "message": f"Leverage cannot exceed suggested: {sizing['suggested_leverage']:.1f}x"}

    # --- BINANCE EXECUTION ---
    try:
        b_symbol = symbol.replace("USD", "USDT") # Convert XAUUSD to XAUUSDT for Binance
        client.futures_change_leverage(symbol=b_symbol, leverage=int(lev_to_use))
        
        # 1. Market Order
        b_side = Client.SIDE_BUY if side == "LONG" else Client.SIDE_SELL
        client.futures_create_order(symbol=b_symbol, side=b_side, type='MARKET', quantity=round(units_to_use, 3))
        
        # 2. Stop Loss (Stop Market)
        stop_side = Client.SIDE_SELL if side == "LONG" else Client.SIDE_BUY
        if sl_type == "SL Points":
            sl_price = entry - sl_value if side == "LONG" else entry + sl_value
        else:
            sl_price = entry * (1 - sl_value/100) if side == "LONG" else entry * (1 + sl_value/100)
        
        client.futures_create_order(symbol=b_symbol, side=stop_side, type='STOP_MARKET', stopPrice=round(sl_price, 2), closePosition=True)

    except Exception as e:
        return {"success": False, "message": f"Binance API Error: {str(e)}"}

    # --- LOG TO SESSION ---
    trade = {
        "timestamp": datetime.utcnow().isoformat(), "date": today, "symbol": symbol, "side": side,
        "entry_price": float(entry), "stop_loss": float(sl_value), "sl_mode": sl_type,
        "units": units_to_use, "leverage": lev_to_use, "risk_usd": sizing["risk_amount"], "status": "open"
    }
    session["trades"].append(trade)
    stats = session["stats"]
    daily = stats.get(today, {"total": 0, "by_symbol": {}})
    daily["total"] += 1
    daily["by_symbol"][symbol] = daily["by_symbol"].get(symbol, 0) + 1
    stats[today] = daily
    session.modified = True

    return {"success": True, "message": f"Order Placed: {units_to_use:.4f} units @ {lev_to_use:.1f}x", "trade": trade}