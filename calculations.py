# logic.py
from flask import session
from datetime import datetime
from calculations import (
    calculate_unutilized_capital,
    lot_size_from_points,
    sl_points_to_percent,
    position_size_from_percent,
    suggested_leverage_from_percent,
    calculate_targets_from_form,
)
import math

# Constants (you can adjust)
TOTAL_CAPITAL_DEFAULT = 10000.0  # default demo capital if not stored in session


def initialize_session():
    """Ensure session keys exist."""
    if "trades" not in session:
        session["trades"] = []
    if "stats" not in session:
        session["stats"] = {}
    if "capital" not in session:
        session["capital"] = TOTAL_CAPITAL_DEFAULT


def _today_iso():
    return datetime.utcnow().date().isoformat()


def calculate_position_sizing(total_capital, entry_price, sl_type, sl_value):
    """
    Returns dict with:
     - sl_percent
     - suggested_lot
     - suggested_position
     - suggested_leverage
     - unutilised
    """
    trades = session.get("trades", [])
    unutilised = calculate_unutilized_capital(total_capital, trades)
    if sl_type == "points":
        if sl_value <= 0:
            return {"error": "SL points must be > 0", "unutilised": unutilised}

        sl_percent = sl_points_to_percent(sl_value, entry_price)
        suggested_lot = lot_size_from_points(unutilised, sl_value)
        suggested_position = position_size_from_percent(unutilised, sl_percent)
        suggested_leverage = suggested_leverage_from_percent(sl_percent)

        return {
            "sl_percent": sl_percent,
            "suggested_lot": suggested_lot,
            "suggested_position": suggested_position,
            "suggested_leverage": suggested_leverage,
            "unutilised": unutilised,
            "error": None,
        }

    elif sl_type == "percent":
        if sl_value <= 0:
            return {"error": "SL % must be > 0", "unutilised": unutilised}

        sl_percent = float(sl_value)
        suggested_lot = None
        suggested_position = position_size_from_percent(unutilised, sl_percent)
        suggested_leverage = suggested_leverage_from_percent(sl_percent)

        return {
            "sl_percent": sl_percent,
            "suggested_lot": suggested_lot,
            "suggested_position": suggested_position,
            "suggested_leverage": suggested_leverage,
            "unutilised": unutilised,
            "error": None,
        }

    else:
        return {"error": "Unknown SL type", "unutilised": unutilised}


def _count_today_trades():
    today = _today_iso()
    trades = session.get("trades", [])
    return len([t for t in trades if t.get("date") == today])


def _count_today_symbol_trades(symbol):
    today = _today_iso()
    trades = session.get("trades", [])
    return len([t for t in trades if t.get("date") == today and t.get("symbol") == symbol])


def execute_trade_action(
    balance,
    symbol,
    side,
    entry,
    sl,  # numeric value (points or percent as provided by form)
    suggested_units,
    suggested_lev,
    user_units,
    user_lev,
    sl_type,
    sl_value,
    order_type,
    tp_list,
    api_key=None,
    api_secret=None,
):
    """
    Validates trade against all rules and, if valid, records it into session["trades"].
    This function does NOT call external brokers — it only simulates/records the trade.
    """

    initialize_session()
    today = _today_iso()
    trades = session["trades"]
    capital = float(session.get("capital", balance))

    # 1) SL must be provided and >0
    if sl_value is None or float(sl_value) <= 0:
        return {"success": False, "msg": "❌ Stoploss is required and must be > 0."}

    # 2) Check daily trade limit (max 4)
    if _count_today_trades() >= 4:
        return {"success": False, "msg": "❌ Daily limit reached (4 trades)."}

    # 3) Max 2 trades per symbol per day
    if _count_today_symbol_trades(symbol) >= 2:
        return {"success": False, "msg": f"❌ Max 2 trades allowed per symbol per day ({symbol})."}

    # 4) Calculate suggested sizing (recompute to avoid tampering)
    sizing = calculate_position_sizing(capital, float(entry), sl_type, float(sl_value))
    if sizing.get("error"):
        return {"success": False, "msg": f"❌ {sizing.get('error')}"}

    # get suggested numbers
    suggested_pos = sizing.get("suggested_position") or 0.0
    suggested_lot = sizing.get("suggested_lot") or 0.0
    suggested_leverage = sizing.get("suggested_leverage")  # may be None

    # Decide what to compare based on what user selected in UI:
    # If user wants lot-based (SL points) allow lot compare; else compare pos size and leverage.
    # Here we conservatively disallow user_units > suggested_pos and user_lev > suggested_leverage when defined.
    if user_units is None or float(user_units) <= 0:
        return {"success": False, "msg": "❌ Units/quantity required."}

    # If suggested_lot exists (i.e., SL points case) then suggested_units = suggested_lot if user chose lot-mode.
    # But to be strict: user_units must not exceed EITHER suggested_lot (if present) OR suggested_pos
    # We enforce both checks: units <= max(suggested_lot, suggested_pos)
    max_allowed_units = max(suggested_pos or 0.0, suggested_lot or 0.0)

    if float(user_units) > (max_allowed_units + 1e-12):
        return {
            "success": False,
            "msg": f"❌ Units exceed suggested max. Suggested units: {max_allowed_units:.6f}"
        }

    # Leverage check (applicable when suggested_leverage is not None)
    if suggested_leverage is not None:
        if user_lev is None or float(user_lev) <= 0:
            return {"success": False, "msg": "❌ Leverage is required (SL% method)."}

        if float(user_lev) > (suggested_leverage + 1e-12):
            return {
                "success": False,
                "msg": f"❌ Leverage exceeds suggested maximum ({suggested_leverage:.2f}x)."
            }

    # 5) Entry type handling: store order_type for later execution simulation (market/limit/stop_market/stop_limit)
    if order_type not in ("market", "limit", "stop_market", "stop_limit"):
        return {"success": False, "msg": "❌ Invalid order type."}

    # Build the trade record
    notional = float(user_units) * float(entry)  # rough notional
    trade = {
        "id": len(trades) + 1,
        "date": today,
        "symbol": symbol,
        "side": side,
        "entry": float(entry),
        "order_type": order_type,
        "sl_type": sl_type,
        "sl_value": float(sl_value),
        "sl_percent": sizing.get("sl_percent"),
        "units": float(user_units),
        "leverage": float(user_lev) if user_lev else None,
        "notional": notional,
        "tp_list": tp_list or [],
        "status": "open",  # open/closed
        "created_at": datetime.utcnow().isoformat(),
    }

    # Save to session
    trades.append(trade)
    session["trades"] = trades  # ensure session persists

    # Update stats (simple daily total)
    stats = session.get("stats", {})
    today_stats = stats.get(today, {"total": 0, "by_symbol": {}})
    today_stats["total"] = today_stats.get("total", 0) + 1
    today_stats["by_symbol"][symbol] = today_stats["by_symbol"].get(symbol, 0) + 1
    stats[today] = today_stats
    session["stats"] = stats

    return {"success": True, "msg": "✅ Trade recorded.", "trade": trade, "sizing": sizing}
