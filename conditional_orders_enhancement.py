from datetime import datetime
import logic
from models import TradePosition, db

def get_tp1_and_sl_orders(user_id):
    """
    Fetch ONLY TP1 and SL orders with position context.
    This allows users to easily find and manually close TP1/SL orders after a trade closes.
    """
    try:
        # 1. Get all open conditional orders from Binance
        all_conditional = logic.get_all_open_conditional_orders(user_id)
        print(f"[DEBUG] all_conditional from logic: {len(all_conditional)} orders")
        
        # 2. Get user's active positions from database to provide context
        # We fetch both open and closed to handle cases where trade just closed
        db_positions = TradePosition.query.filter_by(user_id=user_id).order_by(TradePosition.created_at.desc()).limit(20).all()
        pos_map = {p.symbol: p for p in db_positions}
        
        tp1_orders = []
        sl_orders = []
        
        for o in all_conditional:
            label = o.get('label', '').upper()
            symbol = o.get('symbol')
            
            # Context from DB
            db_pos = pos_map.get(symbol)
            context = {
                'orderId': o.get('orderId'),
                'symbol': symbol,
                'side': o.get('side'),
                'type': o.get('type'),
                'label': o.get('label', ''),
                'triggerPrice': o.get('stopPrice'),
                'qty': o.get('origQty'),
                'time': o.get('time'),
                'position_entry': db_pos.entry_price if db_pos else None,
                'position_sl': db_pos.sl_price if db_pos else None,
                'position_tp1': db_pos.tp1_price if db_pos else None,
                'position_status': db_pos.status if db_pos else 'unknown'
            }
            
            if label == 'TP1' or 'TAKE_PROFIT' in o.get('type', ''):
                tp1_orders.append(context)
            elif label == 'SL' or 'STOP' in o.get('type', '') or 'STOP_LOSS' in o.get('type', ''):
                sl_orders.append(context)
            else:
                print(f"[DEBUG] Order {o.get('orderId')} ({o.get('type')}) skipped - label: {label}")
                
        print(f"[DEBUG] Returning {len(tp1_orders)} TP1 and {len(sl_orders)} SL orders")
        return {
            "success": True,
            "tp1_orders": tp1_orders,
            "sl_orders": sl_orders
        }
    except Exception as e:
        print(f"Error in get_tp1_and_sl_orders: {e}")
        return {
            "success": False,
            "error": str(e),
            "tp1_orders": [],
            "sl_orders": []
        }
