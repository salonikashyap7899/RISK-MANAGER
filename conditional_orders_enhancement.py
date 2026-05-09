import logic
from models import TradePosition, db
import time

def get_tp1_and_sl_orders(user_id):
    """
    ✅ FIXED: Fetch TP1, TP2, and SL orders with live price and position context.
    Returns current_price for each symbol for real-time display.
    """
    try:
        all_conditional = logic.get_all_open_conditional_orders(user_id)
        db_positions = TradePosition.query.filter_by(user_id=user_id).order_by(TradePosition.created_at.desc()).limit(20).all()
        pos_map = {p.symbol: p for p in db_positions}

        tp1_orders, tp2_orders, sl_orders = [], [], []
        price_cache = {}  # Cache live prices to avoid redundant API calls

        for o in all_conditional:
            order_type = o.get('type', '').upper()
            label = o.get('label', '').upper()
            symbol = o.get('symbol', '')

            is_tp2 = (label == 'TP2')
            is_tp1 = (
                label == 'TP1'
                or 'TAKE_PROFIT' in order_type
                or order_type in ('VP', 'TAKE_PROFIT_MARKET', 'TAKE_PROFIT_LIMIT')
            ) and not is_tp2
            is_trail = ('TRAILING' in order_type) or (label == 'TRAIL SL')
            is_sl = (label == 'SL') or is_trail or (
                ('STOP' in order_type or 'STOP_LOSS' in order_type)
                and 'TRAILING' not in order_type
                and not is_tp1
                and not is_tp2
            )

            if not (is_tp1 or is_tp2 or is_sl):
                continue

            db_pos = pos_map.get(symbol)
            
            # ✅ FIXED: Fetch live price once per symbol (cached)
            if symbol not in price_cache:
                try:
                    price_cache[symbol] = logic.get_live_price(symbol, user_id)
                except Exception as e:
                    print(f"⚠️ Error fetching price for {symbol}: {e}")
                    price_cache[symbol] = 0.0
            
            current_price = price_cache[symbol]

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
                'current_price': current_price,  # ✅ ADDED: Live price
                'position_entry': float(db_pos.entry_price) if db_pos and db_pos.entry_price else None,
                'position_sl': float(db_pos.sl_price) if db_pos and db_pos.sl_price else None,
                'position_tp1': float(db_pos.tp1_price) if db_pos and db_pos.tp1_price else None,
                'position_tp2': float(db_pos.tp2_price) if db_pos and db_pos.tp2_price else None,
                'position_status': db_pos.status if db_pos else 'unknown',
            }

            if is_tp1:
                tp1_orders.append(context)
            elif is_tp2:
                tp2_orders.append(context)
            elif is_sl:
                sl_orders.append(context)

        return {
            "success": True, 
            "tp1_orders": tp1_orders, 
            "tp2_orders": tp2_orders, 
            "sl_orders": sl_orders,
            "cached_prices": price_cache  # ✅ Debug info
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {
            "success": False, 
            "error": str(e), 
            "tp1_orders": [], 
            "tp2_orders": [], 
            "sl_orders": []
        }
