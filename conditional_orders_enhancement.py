import logic
from models import TradePosition, db

def get_tp1_and_sl_orders(user_id):
    """
    Fetch TP1 and SL conditional orders with position context.
    Used in the dashboard so users can manually cancel these after a trade closes.

    TP1 = TAKE_PROFIT_MARKET or TAKE_PROFIT type orders (conditional tab on Binance)
    SL  = STOP_MARKET or STOP type orders (conditional tab on Binance)
    TP2 = basic LIMIT order — intentionally excluded here
    """
    try:
        # Step 1: Get all conditional orders already labeled by logic.py
        all_conditional = logic.get_all_open_conditional_orders(user_id)

        # Step 2: Get DB positions for entry/sl/tp1 context (last 20, keyed by symbol)
        db_positions = (
            TradePosition.query
            .filter_by(user_id=user_id)
            .order_by(TradePosition.created_at.desc())
            .limit(20)
            .all()
        )
        
        # FIX: Ensure we keep the NEWEST position for each symbol
        pos_map = {}
        for p in db_positions:
            if p.symbol not in pos_map:
                pos_map[p.symbol] = p

        tp1_orders = []
        sl_orders =[]

        for o in all_conditional:
            order_type = o.get('type', '').upper()
            label = o.get('label', '').upper()
            symbol = o.get('symbol', '')

            # Classify: TP1 = any TAKE_PROFIT order type
            is_tp1 = (label == 'TP1') or ('TAKE_PROFIT' in order_type)
            # Classify: SL = any STOP order type (but NOT trailing stop — that's separate)
            is_sl = (label == 'SL') or (
                ('STOP' in order_type) and ('TRAILING' not in order_type)
            )

            if not is_tp1 and not is_sl:
                continue  # skip TP2, trailing stops, etc.

            # Build context dict with position info from DB
            db_pos = pos_map.get(symbol)
            context = {
                'orderId': o.get('orderId'),
                'symbol': symbol,
                'side': o.get('side'),
                'type': order_type,
                'label': label,
                'triggerPrice': o.get('stopPrice', 0),
                'qty': o.get('origQty', 0),
                'time': o.get('time', 'N/A'),
                'source': o.get('source', 'regular'),
                # Position context from DB (may be None if no DB record)
                'position_entry': float(db_pos.entry_price) if db_pos and db_pos.entry_price else None,
                'position_sl': float(db_pos.sl_price) if db_pos and db_pos.sl_price else None,
                'position_tp1': float(db_pos.tp1_price) if db_pos and db_pos.tp1_price else None,
                'position_status': db_pos.status if db_pos else 'unknown',
            }

            if is_tp1:
                tp1_orders.append(context)
            elif is_sl:
                sl_orders.append(context)

        return {
            "success": True,
            "tp1_orders": tp1_orders,
            "sl_orders": sl_orders,
        }

    except Exception as e:
        print(f"[ERROR] get_tp1_and_sl_orders: {e}")
        return {
            "success": False,
            "error": str(e),
            "tp1_orders": [],
            "sl_orders":[],
        }
