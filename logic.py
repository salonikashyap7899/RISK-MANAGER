# logic.py
from flask import session
from datetime import datetime
from math import ceil

# Note: We import calculate_unutilized_capital here, but its use is primarily
# in app.py for UI display, and not for the core risk calculation logic itself,
# which uses the full balance as per the PyQt app's description.
from calculations import calculate_unutilized_capital

# --- Constants from risk_manager_pyqt.py ---
DAILY_MAX_TRADES = 4
DAILY_MAX_PER_SYMBOL = 2
RISK_PERCENT = 1.0  # fixed 1%
TOTAL_CAPITAL_DEFAULT = 10000.0
# ------------------------------------------

def initialize_session():
    """Ensure session keys exist, including 'capital'."""
    if "trades" not in session:
        session["trades"] = []
    if "stats" not in session:
        session["stats"] = {}
    if "capital" not in session:
        session["capital"] = TOTAL_CAPITAL_DEFAULT

def _today_iso():
    return datetime.utcnow().date().isoformat()

# --- CORE RISK LOGIC (PyQt's recalculate method) ---
def calculate_position_sizing(balance, entry, sl_type, sl_value):
    """
    Calculates suggested position size and leverage based on 1% risk.
    """
    
    risk_amount = balance * (RISK_PERCENT / 100.0)
    sl_distance = 0.0
    max_leverage_info = "N/A"
    
    try:
        entry = float(entry)
        sl_value = float(sl_value)
    except ValueError:
        return {"error": "Invalid Entry Price or SL Value."}

    # 1. SL Distance Calculation (with the required offsets from PyQt)
    if sl_type == "SL Points":
        sl_distance = sl_value + 20.0 
        
    elif sl_type == "SL % Movement":
        sl_percent_total = sl_value + 0.2
        
        # Max Leverage Feature (100 / SL%)
        if sl_value > 0:
            max_leverage = 100.0 / sl_value
            max_leverage_info = f"{max_leverage:.1f}x"
        
        # SL distance (in currency terms)
        sl_distance = (sl_percent_total / 100.0) * entry

    if sl_distance <= 0:
        return {
            "error": "SL distance is invalid or too small.",
            "suggested_units": 0.0,
            "suggested_leverage": 0.0,
            "max_leverage_info": max_leverage_info,
            "risk_amount": risk_amount,
        }

    # 2. Calculate Suggested Units (Position Size / Lot Size)
    suggested_units_val = risk_amount / sl_distance

    # 3. Calculate Suggested Leverage
    position_notional = suggested_units_val * entry
    
    if balance > 0:
        suggested_leverage_val = position_notional / balance
    else:
        suggested_leverage_val = 0.0
    
    # Round leverage up to the nearest 0.5 (PyQt logic)
    rounded_leverage = ceil(suggested_leverage_val * 2) / 2.0

    return {
        "suggested_units": suggested_units_val,
        "suggested_leverage": rounded_leverage,
        "risk_amount": risk_amount,
        "max_leverage_info": max_leverage_info,
        "notional": position_notional,
        "error": None
    }


# --- TRADE EXECUTION AND VALIDATION (PyQt's execute_trade_action) ---
# **THIS SIGNATURE IS CRITICAL AND MATCHES THE FIXED app.py CALL**
def execute_trade_action(
    balance, symbol, side, entry, sl_type, sl_value, order_type, tp_list,
    sizing, user_units, user_lev
):
    """Simulates trade execution after validation against risk and daily limits."""
    
    suggested_units = sizing.get("suggested_units", 0.0)
    suggested_lev = sizing.get("suggested_leverage", 0.0)
    risk_amount = sizing.get("risk_amount", 0.0)
    
    # --- 1. Daily Limit Check ---
    today = _today_iso()
    trades = session.get("trades", [])
    current_trades = len([t for t in trades if t.get("date") == today])
    symbol_trades = len([t for t in trades if t.get("date") == today and t.get("symbol") == symbol])
    
    if current_trades >= DAILY_MAX_TRADES:
        return {"success": False, "message": f"DAILY LIMIT REACHED: Max {DAILY_MAX_TRADES} total trades."}
    if symbol_trades >= DAILY_MAX_PER_SYMBOL:
        return {"success": False, "message": f"SYMBOL LIMIT REACHED: Max {DAILY_MAX_PER_SYMBOL} trades for {symbol}."}

    # --- 2. Risk Validation Check (PyQt's override block logic) ---
    
    # Use suggested values if override is 0.0
    units_to_use = user_units if user_units > 0 else suggested_units
    leverage_to_use = user_lev if user_lev > 0 else suggested_lev
    
    # Check if override STRICTLY exceeds suggested values (1e-6 tolerance)
    if user_units > suggested_units + 1e-6: 
        return {"success": False, "message": f"Lot Size ({user_units:,.4f}) **exceeds** Max Suggested ({suggested_units:,.4f}) set by 1% risk."}
    if user_lev > suggested_lev + 1e-6:
        return {"success": False, "message": f"Leverage ({user_lev:.1f}x) **exceeds** Max Suggested ({suggested_lev:.1f}x) based on 1% risk."}
    
    if units_to_use <= 0 or leverage_to_use <= 0:
        return {"success": False, "message": "Position/Lot Size and Leverage must be positive values."}

    # --- 3. Execution (Logging) ---
    notional = units_to_use * entry 

    trade = {
        "timestamp": datetime.now().isoformat(),
        "date": today,
        "symbol": symbol,
        "side": side,
        "order_type": order_type,
        "entry_price": entry,
        "stop_loss": sl_value, 
        "sl_mode": sl_type,
        "units": units_to_use,
        "leverage": leverage_to_use,
        "tp_list": tp_list, 
        "risk_usd": risk_amount,
        "notional": notional, 
        "status": "open"
    }
    
    # Update Session Data
    session["trades"].append(trade)
    
    # Update Daily Stats in Session
    stats = session.get("stats", {})
    daily_stats = stats.get(today, {"total": 0, "by_symbol": {}})
    daily_stats["total"] += 1
    daily_stats["by_symbol"][symbol] = daily_stats["by_symbol"].get(symbol, 0) + 1
    stats[today] = daily_stats
    session["stats"] = stats
    session.modified = True 
    
    return {
        "success": True, 
        "message": (
            f"Order for {units_to_use:,.4f} {symbol} placed at {leverage_to_use:.1f}x leverage. "
            f"Max Risk: ${risk_amount:,.2f}"
        ),
        "trade": trade,
    }