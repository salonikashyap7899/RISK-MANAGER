# logic.py (FINAL UPGRADED VERSION)
from flask import session
from datetime import datetime
from math import ceil

DAILY_MAX_TRADES = 4
DAILY_MAX_PER_SYMBOL = 2
RISK_PERCENT = 1.0          # 1%
TOTAL_CAPITAL_DEFAULT = 10000.0


def initialize_session():
    if "trades" not in session:
        session["trades"] = []
    if "stats" not in session:
        session["stats"] = {}
    if "capital" not in session:
        session["capital"] = TOTAL_CAPITAL_DEFAULT


def _today_iso():
    return datetime.utcnow().date().isoformat()


# ===========================================
#     POSITION SIZING (All Rules Applied)
# ===========================================
def calculate_position_sizing(balance, entry, sl_type, sl_value):
    try:
        entry = float(entry)
        sl_value = float(sl_value)
    except:
        return {"error": "Invalid Entry or SL"}

    # 1% RISK from unutilized capital (The input 'balance' is now the unutilised capital from app.py)
    risk_amount = balance * (RISK_PERCENT / 100.0)

    # --------------------------
    # SL POINTS MODE
    # --------------------------
    if sl_type == "SL Points":
        if sl_value <= 0:
            return {"error": "SL Required"}
        
        # Risk Calculation: Lot Suggested = (1% of unutilised) / (SL Points + 20 Pts)
        sl_distance_pts = sl_value + 20
        suggested_units = risk_amount / sl_distance_pts # This is lot size
        
        # Leverage Calculation (Point 3a):
        # 1. SL % Movement (with buffer) = ((SL Points + 20) / Entry Price) * 100
        sl_percent_for_lev = (sl_distance_pts / entry) * 100 if entry > 0 else 0
        
        # 2. Leverage = 100 / SL % Movement (derived from SL Points)
        suggested_lev = 100 / sl_percent_for_lev if sl_percent_for_lev > 0 else 0
        suggested_lev = ceil(suggested_lev * 2) / 2  # Round up to nearest 0.5x

        return {
            "suggested_units": suggested_units, # This is Lot Size
            "suggested_leverage": suggested_lev,
            "risk_amount": risk_amount,
            "max_leverage_info": f"{suggested_lev:.1f}x",
            "error": None
        }

    # --------------------------
    # SL % MOVEMENT MODE
    # --------------------------
    elif sl_type == "SL % Movement":
        if sl_value <= 0:
            return {"error": "SL Required"}

        # Leverage Calculation (Point 3b): Use SL% Movement + 0.2% buffer
        sl_distance_pct = sl_value + 0.2
        suggested_lev = 100 / sl_distance_pct
        suggested_lev = ceil(suggested_lev * 2) / 2  # Round up to nearest 0.5x

        # Position Size Calculation (Point 2): 
        # Notional = (1% Risk / (SL% Distance / 100))
        suggested_notional = risk_amount / (sl_distance_pct / 100)
        
        # Units (Lot) = Notional / Entry Price
        suggested_units = suggested_notional / entry if entry > 0 else 0 

        return {
            "suggested_units": suggested_units, # This is Position Size/Notional in terms of units
            "suggested_leverage": suggested_lev,
            "risk_amount": risk_amount,
            "max_leverage_info": f"{suggested_lev:.1f}x",
            "error": None
        }

    return {"error": "Invalid SL Type"}


# ===========================================
#     TRADE EXECUTION RULES
# ===========================================
def execute_trade_action(
    balance, symbol, side, entry, sl_type, sl_value, order_type,
    tp_list, sizing, user_units, user_lev
):
    initialize_session()

    # SL mandatory rule
    if float(sl_value) <= 0:
        return {"success": False, "message": "SL REQUIRED — Trade cannot be placed."}

    # Daily limits
    today = _today_iso()
    trades = session["trades"]

    todays = [t for t in trades if t["date"] == today]
    if len(todays) >= DAILY_MAX_TRADES:
        return {"success": False, "message": "Daily 4 trades limit reached."}

    symbol_trades = [t for t in todays if t["symbol"] == symbol]
    if len(symbol_trades) >= DAILY_MAX_PER_SYMBOL:
        return {
            "success": False,
            "message": f"Max 2 daily trades allowed for {symbol}."
        }

    if not sizing or sizing.get("error"):
        return {"success": False, "message": sizing.get("error", "Invalid sizing")}

    suggested_units = sizing["suggested_units"]
    suggested_lev = sizing["suggested_leverage"]
    risk_amount = sizing["risk_amount"]

    # Override rules
    units_to_use = user_units if user_units > 0 else suggested_units
    lev_to_use = user_lev if user_lev > 0 else suggested_lev

    # Reject if > suggested
    if user_units > suggested_units:
        return {
            "success": False,
            "message": f"Lot Size cannot exceed suggested: {suggested_units:.4f}"
        }

    if user_lev > suggested_lev:
        return {
            "success": False,
            "message": f"Leverage cannot exceed suggested: {suggested_lev:.1f}x"
        }

    # Final Trade Record
    trade = {
        "timestamp": datetime.utcnow().isoformat(),
        "date": today,
        "symbol": symbol,
        "side": side,
        "order_type": order_type,
        "entry_price": float(entry),
        "stop_loss": float(sl_value),
        "sl_mode": sl_type,
        "units": units_to_use,
        "leverage": lev_to_use,
        "tp_list": tp_list or [],
        "risk_usd": risk_amount,
        "status": "open"
    }

    session["trades"].append(trade)

    # Update stats
    stats = session["stats"]
    daily = stats.get(today, {"total": 0, "by_symbol": {}})
    daily["total"] += 1
    daily["by_symbol"][symbol] = daily["by_symbol"].get(symbol, 0) + 1
    stats[today] = daily
    session["stats"] = stats
    session.modified = True

    return {
        "success": True,
        "message": f"Order Placed: {units_to_use:.4f} units @ {lev_to_use:.1f}x",
        "trade": trade
    }