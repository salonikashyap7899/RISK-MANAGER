import logic
from models import TradePosition, db

def get_tp1_and_sl_orders(user_id):
    try:
        all_conditional = logic.get_all_open_conditional_orders(user_id)
        db_positions = TradePosition.query.filter_by(user_id=user_id).order_by(TradePosition.created_at.desc()).limit(20).all()
        pos_map = {p.symbol: p for p in db_positions}

        tp1_orders, tp2_orders, sl_orders = [], [], []

        for o in all_conditional:
            order_type = o.get('type', '').upper()
            label = o.get('label', '').upper()
            symbol = o.get('symbol', '')

            is_tp2 = (label == 'TP2')
            is_tp1 = (label == 'TP1') or ('TAKE_PROFIT' in order_type and not is_tp2)
            is_trail = ('TRAILING' in order_type) or (label == 'TRAIL SL')
            is_sl = (label == 'SL') or is_trail or (
                ('STOP' in order_type or 'STOP_LOSS' in order_type)
                and 'TRAILING' not in order_type
                and label not in ('TP1', 'TP2')
            )

            if not (is_tp1 or is_tp2 or is_sl):
                continue

            db_pos = pos_map.get(symbol)
            context = {
                'orderId': o.get('orderId'),
                'symbol': symbol,
                'side': o.get('side'),
                'type': order_type,
                'label': label,
                'triggerPrice': o.get('stopPrice', 0),
                'price': o.get('price', 0),
                'qty': o.get('origQty', 0),
                'time': o.get('time', 'N/A'),
                'source': o.get('source', 'regular'),
                'position_entry': float(db_pos.entry_price) if db_pos and db_pos.entry_price else None,
                'position_sl': float(db_pos.sl_price) if db_pos and db_pos.sl_price else None,
                'position_tp1': float(db_pos.tp1_price) if db_pos and db_pos.tp1_price else None,
                'position_status': db_pos.status if db_pos else 'unknown',
            }

            if is_tp1:
                tp1_orders.append(context)
            elif is_tp2:
                tp2_orders.append(context)
            elif is_sl:
                sl_orders.append(context)

        # FIX: Fallback to direct fetch if no orders found via logic.py
        if not tp1_orders and not sl_orders:
            client = logic.get_client(user_id)
            if client:
                try:
                    raw = client.futures_get_open_orders(recvWindow=10000)
                    for o in raw:
                        o_type = o.get('type', '').upper()
                        has_stop = float(o.get('stopPrice', 0)) > 0
                        if o_type in ['STOP_MARKET', 'TAKE_PROFIT_MARKET', 'TRAILING_STOP_MARKET'] or has_stop:
                            symbol = o.get('symbol', '')
                            db_pos = pos_map.get(symbol)
                            label = 'SL'
                            if 'TAKE_PROFIT' in o_type: label = 'TP1'
                            
                            context = {
                                'orderId': o.get('orderId'),
                                'symbol': symbol,
                                'side': o.get('side'),
                                'type': o_type,
                                'label': label,
                                'triggerPrice': float(o.get('stopPrice', 0)),
                                'price': float(o.get('price', 0)),
                                'qty': float(o.get('origQty', 0)),
                                'time': 'Direct-Fetch',
                                'source': 'fallback',
                                'position_entry': float(db_pos.entry_price) if db_pos else None,
                                'position_status': db_pos.status if db_pos else 'unknown',
                            }
                            if label == 'TP1': tp1_orders.append(context)
                            else: sl_orders.append(context)
                except Exception as fe:
                    print(f"[fallback] direct fetch failed: {fe}")

        return {"success": True, "tp1_orders": tp1_orders, "tp2_orders": tp2_orders, "sl_orders": sl_orders}
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"success": False, "error": str(e), "tp1_orders": [], "tp2_orders": [], "sl_orders": []}
