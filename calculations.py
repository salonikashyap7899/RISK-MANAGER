# calculations.py
from datetime import datetime


def calculate_unutilized_capital(total_capital, session_trades):
    """
    Calculate unutilised capital = total_capital - margin_used_by_open_trades
    We'll treat margin used roughly as the notional of open trades saved in session.
    session_trades: list of trade dicts with 'notional' and 'status' keys.
    Only 'open' trades count for margin.
    """
    used = 0.0
    for t in session_trades:
        if t.get("status", "open") == "open":
            used += float(t.get("notional", 0) or 0)
    return max(total_capital - used, 0.0)


def lot_size_from_points(unutilised_capital, sl_points):
    """
    Lot Size formula:
    lot = (1% of unutilised capital) / (sl_points + 20)
    """
    if sl_points <= 0:
        return 0.0
    risk = unutilised_capital * 0.01
    return risk / (sl_points + 20)


def sl_points_to_percent(sl_points, entry_price):
    """
    Convert SL in points to percent:
    sl_percent = (sl_points / entry_price) * 100
    """
    if entry_price <= 0:
        return 0.0
    return (float(sl_points) / float(entry_price)) * 100.0


def position_size_from_percent(unutilised_capital, sl_percent):
    """
    Position size formula (works for SL%):
    position_size = (1% of unutilised capital) / (sl_percent + 0.2)
    Note: sl_percent is in percent (e.g. 0.5 for 0.5%)
    """
    if sl_percent <= 0:
        return 0.0
    risk = unutilised_capital * 0.01
    # sl_percent + 0.2 (both in percent)
    return risk / (sl_percent + 0.2)


def suggested_leverage_from_percent(sl_percent):
    """
    Suggested leverage = 100 / SL%
    (If sl_percent is 0.5 (0.5%), returns 200)
    """
    if sl_percent <= 0:
        return None
    return 100.0 / sl_percent


def calculate_targets_from_form(tp1_price, tp1_percent, tp2_price):
    """
    Normalize TP inputs. Returns dict with tp1/tp2 prices and percents.
    If tp1_percent provided, tp2_percent = 100 - tp1_percent
    """
    tp1_percent = int(tp1_percent or 0)
    remaining = max(0, 100 - tp1_percent)
    tp_list = []
    if tp1_price and tp1_percent > 0:
        tp_list.append({"price": float(tp1_price), "percentage": tp1_percent})
    if tp2_price and remaining > 0:
        tp_list.append({"price": float(tp2_price), "percentage": remaining})
    return tp_list
