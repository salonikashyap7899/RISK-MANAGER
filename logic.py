# logic.py (fixed)
from flask import session
from datetime import datetime
from math import ceil

from calculations import calculate_unutilized_capital

# --- Constants ---
DAILY_MAX_TRADES = 4
DAILY_MAX_PER_SYMBOL = 2
RISK_PERCENT = 1.0  # fixed 1%
TOTAL_CAPITAL_DEFAULT = 10000.0
# ------------------

def initialize_session():
    """Ensure session keys exist, including 'capital'."""
    if "trades" not in session or not isinstance(session["trades"], list):
        session["trades"] = []
    if "stats" not in session or not isinstance(session["stats"], dict):
        session["stats"] = {}
    if "capital" not in session:
        session["capital"] = TOTAL_CAPITAL_DEFAULT


def _today_iso():
    # Use UTC date consistently across app
    return datetime.utcnow().date().isoformat()


def calculate_position_sizing(balance, entry, sl_type, sl_value):
    """
    Calculates suggested position size and leverage based on 1% risk.
    Returns a dict with keys:
      suggested_units, suggested_leverage, risk_amount, max_leverage_info, notional, error
    """
    # Defensive defaults
    try:
        entry = float(entry)
        sl_value = float(sl_value)
    except Exception:
        return {
            "suggested_units": 0.0,
            "suggested_leverage": 0.0,
            "risk_amount": 0.0,
            "max_leverage_info": "N/A",
            "notional": 0.0,
            "error": "Invalid Entry Price or SL Value."
        }

    risk_amount = float(balance) * (RISK_PERCENT / 100.0)
    sl_distance = 0.0
    max_leverage_info = "N/A"

    if sl_type == "SL Points":
        # PyQt had a fixed offset of 20 pts added
        sl_distance = sl_value + 20.0

    elif sl_type == "SL % Movement":
        # small buffer like PyQt: +0.2
        sl_percent_total = sl_value + 0.2
        if sl_value > 0:
            max_leverage = 100.0 / sl_value
            max_leverage_info = f"{max_leverage:.1f}x"
        sl_distance = (sl_percent_total / 100.0) * entry

    # Validate sl distance
    if sl_distance <= 0:
        return {
            "suggested_units": 0.0,
            "suggested_leverage": 0.0,
            "risk_amount": risk_amount,
            "max_leverage_info": max_leverage_info,
            "notional": 0.0,
            "error": "SL distance is invalid or too small."
        }

    # Suggested units (position size) = risk_amount / sl_distance
    suggested_units_val = risk_amount / sl_distance
    position_notional = suggested_units_val * entry

    # Suggested leverage = notional / balance
    suggested_leverage_val = (position_notional / balance) if balance > 0 else 0.0
    # Round up to nearest 0.5 per PyQt logic
    rounded_leverage = ceil(suggested_leverage_val * 2.0) / 2.0 if suggested_leverage_val > 0 else 0.0

    return {
        "suggested_units": suggested_units_val,
        "suggested_leverage": rounded_leverage,
        "risk_amount": risk_amount,
        "max_leverage_info": max_leverage_info,
        "notional": position_notional,
        "error": None
    }


def execute_trade_action(
    balance, symbol, side, entry, sl_type, sl_value, order_type, tp_list,
    sizing, user_units, user_lev
):
    """
    Simulates trade execution after validation against risk and daily limits.
    Returns dict with success (bool), message (str), and optional 'trade' dict.
    """
    initialize_session()  # make sure session keys exist

    # Defensive sizing check
    if not sizing or not isinstance(sizing, dict):
        return {"success": False, "message": "Sizing not calculated. Cannot place trade."}

    suggested_units = float(sizing.get("suggested_units", 0.0) or 0.0)
    suggested_lev = float(sizing.get("suggested_leverage", 0.0) or 0.0)
    risk_amount = float(sizing.get("risk_amount", 0.0) or 0.0)

    # --- 1. Daily Limit Check ---
    today = _today_iso()
    trades = session.get("trades", [])
    # count today's trades
    current_trades = len([t for t in trades if t.get("date") == today])
    symbol_trades = len([t for t in trades if t.get("date") == today and t.get("symbol") == symbol])

    if current_trades >= DAILY_MAX_TRADES:
        return {"success": False, "message": f"DAILY LIMIT REACHED: Max {DAILY_MAX_TRADES} total trades."}
    if symbol_trades >= DAILY_MAX_PER_SYMBOL:
        return {"success": False, "message": f"SYMBOL LIMIT REACHED: Max {DAILY_MAX_PER_SYMBOL} trades for {symbol}."}

    # --- 2. Risk Validation / Overrides ---
    units_to_use = float(user_units) if float(user_units or 0.0) > 0 else suggested_units
    leverage_to_use = float(user_lev) if float(user_lev or 0.0) > 0 else suggested_lev

    # If suggested is zero (bad sizing) then refuse
    if suggested_units <= 0 or suggested_lev <= 0:
        return {"success": False, "message": "Invalid sizing - suggested units or leverage is zero."}

    # User overrides cannot exceed suggested values (strict)
    if float(user_units or 0.0) > suggested_units + 1e-6:
        return {"success": False, "message": f"Lot Size ({float(user_units):,.4f}) exceeds Max Suggested ({suggested_units:,.4f}) set by 1% risk."}
    if float(user_lev or 0.0) > suggested_lev + 1e-6:
        return {"success": False, "message": f"Leverage ({float(user_lev):.1f}x) exceeds Max Suggested ({suggested_lev:.1f}x) based on 1% risk."}

    if units_to_use <= 0 or leverage_to_use <= 0:
        return {"success": False, "message": "Position/Lot Size and Leverage must be positive values."}

    # --- 3. Execution (Logging) ---
    notional = units_to_use * float(entry)

    trade = {
        "timestamp": datetime.utcnow().isoformat(),   # use UTC ISO consistently
        "date": today,
        "symbol": symbol,
        "side": side,
        "order_type": order_type,
        "entry_price": float(entry),
        "stop_loss": float(sl_value),
        "sl_mode": sl_type,
        "units": units_to_use,
        "leverage": leverage_to_use,
        "tp_list": tp_list or [],
        "risk_usd": risk_amount,
        "notional": notional,
        "status": "open"
    }

    # Ensure trades list exists and append
    if "trades" not in session or not isinstance(session["trades"], list):
        session["trades"] = []
    session["trades"].append(trade)

    # Mark session modified so client session persists
    session.modified = True

    # Update Daily Stats in Session
    stats = session.get("stats", {})
    daily_stats = stats.get(today, {"total": 0, "by_symbol": {}})
    daily_stats["total"] = daily_stats.get("total", 0) + 1
    by_symbol = daily_stats.get("by_symbol", {})
    by_symbol[symbol] = by_symbol.get(symbol, 0) + 1
    daily_stats["by_symbol"] = by_symbol
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
