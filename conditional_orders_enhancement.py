import logic
from models import TradePosition, db
from datetime import datetime

def get_tp1_and_sl_orders(user_id):
    try:
        client = logic.get_client(user_id)
        if not client:
            return {"success": False, "tp1_orders": [], "tp2_orders": [], "sl_orders": []}
        
        # 1. Fetch Regular & Algo orders from Binance
        all_binance_orders = []
        try:
            # Regular Open Orders (Limit, Stop Market)
            reg_orders = client.futures_get_open_orders(recvWindow=10000)
            for o in reg_orders:
                o['order_source'] = 'regular'
                all_binance_orders.append(o)
                
            # Algo Orders (Take Profit Market, Trailing Stops)
            if hasattr(client, 'futures_get_algo_orders'):
                algo_resp = client.futures_get_algo_orders(recvWindow=10000)
                algo_list = algo_resp if isinstance(algo_resp, list) else algo_resp.get('orders', [])
                for o in algo_list:
                    o['order_source'] = 'algo'
                    all_binance_orders.append(o)
        except Exception as e:
            print(f"Fetch Error: {e}")

        tp1_orders, tp2_orders, sl_orders = [], [], []
        seen_labels = set() # key: symbol_label

        for o in all_binance_orders:
            # IMPORTANT: Capture the correct ID
            oid = o.get('algoId') or o.get('orderId')
            if not oid: continue
            
            symbol = o.get('symbol', '')
            o_type = (o.get('type') or o.get('algoType') or '').upper()
            trigger = float(o.get('stopPrice') or o.get('triggerPrice') or o.get('price') or 0)
            qty = float(o.get('origQty') or o.get('qty') or 0)
            side = o.get('side', '').upper()
            
            # Classification
            label = "ORDER"
            if 'TAKE_PROFIT' in o_type:
                label, target_list = 'TP1', tp1_orders
            elif 'STOP' in o_type or 'TRAILING' in o_type:
                label, target_list = 'SL', sl_orders
            elif o_type == 'LIMIT' and (o.get('reduceOnly') or o.get('closePosition')):
                label, target_list = 'TP2', tp2_orders
            else: continue

            seen_labels.add(f"{symbol}_{label}")
            
            target_list.append({
                'orderId': str(oid), # Ensure string for frontend consistency
                'symbol': symbol,
                'side': side,
                'type': o_type,
                'label': label,
                'triggerPrice': trigger,
                'qty': qty,
                'time': 'Binance Live',
                'source': o['order_source'] # important for cancel logic
            })

        # 2. Add Virtual Guards only for missing slots
        db_pos = TradePosition.query.filter_by(user_id=user_id, status='open').all()
        for p in db_pos:
            side_close = 'SELL' if p.side == 'LONG' else 'BUY'
            if p.sl_price > 0 and f"{p.symbol}_SL" not in seen_labels:
                sl_orders.append({'orderId': f"virtual_sl_{p.id}", 'symbol': p.symbol, 'side': side_close, 'type': 'VIRTUAL', 'label': 'SL', 'triggerPrice': float(p.current_sl or p.sl_price), 'qty': float(p.initial_qty), 'time': 'System Guard', 'source': 'virtual'})
            if p.tp1_price > 0 and f"{p.symbol}_TP1" not in seen_labels:
                tp1_orders.append({'orderId': f"virtual_tp1_{p.id}", 'symbol': p.symbol, 'side': side_close, 'type': 'VIRTUAL', 'label': 'TP1', 'triggerPrice': float(p.tp1_price), 'qty': float(p.initial_qty) * 0.5, 'time': 'System Guard', 'source': 'virtual'})

        return {"success": True, "tp1_orders": tp1_orders, "tp2_orders": tp2_orders, "sl_orders": sl_orders}
    except Exception as e:
        return {"success": False, "error": str(e), "tp1_orders": [], "tp2_orders": [], "sl_orders": []}
