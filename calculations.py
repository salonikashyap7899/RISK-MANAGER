# calculations.py
from datetime import datetime

# NOTE: The import from logic has been removed to fix the circular import.

def calculate_unutilized_capital(total_capital, session_trades):
    """
    Calculates the total margin used by all 'open' trades.
    We'll treat margin used roughly as the notional of open trades saved in session.
    """
    used = 0.0
    for t in session_trades:
        # Check for 'open' status and accumulate notional
        if t.get("status", "open") == "open":
            used += float(t.get("notional", 0) or 0)
    # The PyQt app uses the full capital for risk calculation, 
    # but we return the used margin here for the UI display.
    return used


def calculate_targets_from_form(tp1_price, tp1_percent, tp2_price):
    """
    Normalizes TP inputs to match the trade record structure.
    """
    tp_list = []
    
    tp1_price = float(tp1_price or 0.0)
    tp1_percent = float(tp1_percent or 0.0)
    tp2_price = float(tp2_price or 0.0)

    # TP 1
    if tp1_price > 0 and tp1_percent > 0:
        tp_list.append({
            "price": tp1_price, 
            "percent_position": tp1_percent
        })
    
    # TP 2 (Assumed to be the full exit if no other TPs are set, or the remainder)
    if tp2_price > 0:
        tp1_pct = tp1_percent
        remaining_pct = max(0.0, 100.0 - tp1_pct)
        
        if remaining_pct > 0.0 or not tp_list: 
             tp_list.append({
                "price": tp2_price, 
                "percent_position": remaining_pct if tp_list else 100.0
            })
            
    return tp_list