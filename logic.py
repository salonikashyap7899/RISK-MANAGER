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

def get_all_exchange_symbols():
    try:
        info = client.futures_exchange_info()
        return sorted([s['symbol'] for s in info['symbols'] if s['status'] == 'TRADING' and s['quoteAsset'] == 'USDT'])
    except:
        return ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

def get_live_balance():
    """Fetches real USDT balance from your live wallet."""
    try:
        acc = client.futures_account()
        # Look for USDT specifically in the assets list
        usdt_data = next((item for item in acc.get('assets', []) if item["asset"] == "USDT"), None)
        if usdt_data:
            # availableBalance is your actual spendable money
            available = float(usdt_data.get('availableBalance', 0))
            margin_locked = float(usdt_data.get('initialMargin', 0))
            return (available + margin_locked), margin_locked
        return None, None
    except:
        return None, None

def get_live_price(symbol):
    try:
        ticker = client.futures_symbol_ticker(symbol=symbol)
        return float(ticker['price'])
    except:
        return None

def calculate_position_sizing(unutilized_margin, entry, sl_type, sl_value):
    """
    Formulas implemented:
    Max Leverage = 100 / (SL% + 0.2%) [Cap at 100]
    Pos Size = {Risk / (SL% + 0.2%)} * 100
    """
    try:
        if sl_value <= 0: return {"error": "SL Required"}
        
        entry = float(entry)
        risk_amount = unutilized_margin * 0.01
        
        # Determine SL percentage
        if sl_type == "SL Points":
            sl_pct = (sl_value / entry) * 100
        else:
            sl_pct = sl_value

        movement_factor = sl_pct + 0.2
        
        # 1. Position Sizing Formula
        notional_value = (risk_amount / movement_factor) * 100
        suggested_units = notional_value / entry
        
        # 2. Suggested Leverage Formula
        raw_lev = 100 / movement_factor
        suggested_lev = min(100.0, floor(raw_lev))
        
        return {
            "suggested_units": round(suggested_units, 3),
            "suggested_leverage": int(max(1, suggested_lev)),
            "risk_amount": round(risk_amount, 2),
            "error": None
        }
    except:
        return {"error": "Invalid Input"}

def execute_trade_action(balance, symbol, side, entry, order_type, sl_type, sl_value, sizing, u_units, u_lev, margin_mode, tp1, tp1_pct, tp2):
    today = datetime.utcnow().date().isoformat()
    day_stats = session["stats"].get(today, {"total": 0, "symbols": {}})
    
    # 4 Global Trade Limit
    if day_stats["total"] >= 4:
        return {"success": False, "message": "REJECTED: Daily limit of 4 trades reached."}
    
    # 2 Per Symbol Trade Limit
    if day_stats["symbols"].get(symbol, 0) >= 2:
        return {"success": False, "message": f"REJECTED: Limit (2/day) reached for {symbol}."}

    try:
        units = u_units if u_units > 0 else sizing["suggested_units"]
        lev = int(u_lev if u_lev > 0 else sizing["suggested_leverage"])

        client.futures_change_leverage(symbol=symbol, leverage=lev)
        
        # Execution code remains the same as your current project
        b_side = Client.SIDE_BUY if side == "LONG" else Client.SIDE_SELL
        if order_type == "MARKET":
            client.futures_create_order(symbol=symbol, side=b_side, type='MARKET', quantity=abs(round(units, 3)))
        else:
            client.futures_create_order(symbol=symbol, side=b_side, type='LIMIT', timeInForce='GTC', quantity=abs(round(units, 3)), price=str(entry))

        # Update stats after success
        day_stats["total"] += 1
        day_stats["symbols"][symbol] = day_stats["symbols"].get(symbol, 0) + 1
        session["stats"][today] = day_stats
        session["trades"].append({"timestamp": datetime.utcnow().isoformat(), "symbol": symbol, "side": side, "entry_price": entry, "units": units})
        session.modified = True
        return {"success": True, "message": f"SUCCESS: {side} {symbol} placed."}
    except Exception as e:
        return {"success": False, "message": str(e)}