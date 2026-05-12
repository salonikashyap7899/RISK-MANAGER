import logic
from models import TradePosition, db
from datetime import datetime

def get_tp1_and_sl_orders(user_id):
    try:
        client = logic.get_client(user_id)
        if not client: return {"success": False, "tp1_orders":[], "tp2_orders":[], "sl_orders":[]}

        # 1. Fetch ALL data from Binance endpoints
        all_regular = []
        all_algo = []
        try: all_regular = client.futures_get_open_orders(recvWindow=10000)
        except: pass
        try:
            if hasattr(client, 'futures_get_algo_orders'):
                resp = client.futures_get_algo_orders(recvWindow=10000)
                all_algo = resp if isinstance(resp, list) else resp.get('orders', [])
            elif hasattr(client, '_request_futures_api'):
                resp = client._request_futures_api('get', 'algoOrder/openOrders', True, data={'recvWindow': 10000})
                all_algo = resp if isinstance(resp, list) else resp.get('orders', [])
        except: pass

        tp1_orders, tp2_orders, sl_orders = [], [], []
        seen_binance_types = set() # Track sym_label to avoid virtual duplicates

        def parse_binance_order(o, is_algo=False):
            # IDs: algo orders use algoId, others use orderId
            oid = o.get('algoId') or o.get('orderId')
            if not oid: return
            
            sym = o.get('symbol', '')
            o_type = str(o.get('type') or o.get('algoType') or '').upper()
            
            # Fetch Price/Trigger Price
            price = float(o.get('stopPrice') or o.get('triggerPrice') or o.get('price') or 0)
            qty = float(o.get('origQty') or o.get('qty') or 0)
            
            # Determine classification (SL vs TP1 vs TP2)
            if "TAKE_PROFIT" in o_type:
                label, list_to_add = "TP1", tp1_orders
            elif "STOP" in o_type or "TRAILING" in o_type:
                label, list_to_add = "SL", sl_orders
            elif o_type == "LIMIT" and (o.get('reduceOnly') or o.get('closePosition')):
                label, list_to_add = "TP2", tp2_orders
            else:
                return # Don't show entry/plain limit orders

            seen_binance_types.add(f"{sym}_{label}")
            
            list_to_add.append({
                'orderId': oid,
                'symbol': sym,
                'side': o.get('side', '').upper(),
                'type': o_type,
                'label': label,
                'triggerPrice': price,
                'qty': qty,
                'time': datetime.now().strftime('%H:%M:%S'), # Recent fetch time
                'source': 'binance'
            })

        # Process everything from Binance first
        for o in all_regular: parse_binance_order(o)
        for o in all_algo: parse_binance_order(o, is_algo=True)

        # 2. Add Virtual Guards only if Binance is empty for that symbol/slot
        db_pos = TradePosition.query.filter_by(user_id=user_id, status='open').all()
        for p in db_pos:
            s_close = 'SELL' if p.side == 'LONG' else 'BUY'
            
            if p.sl_price > 0 and f"{p.symbol}_SL" not in seen_binance_types:
                sl_orders.append({
                    'orderId': f"virtual_sl_{p.id}",
                    'symbol': p.symbol, 'side': s_close, 'type': 'VIRTUAL_GUARD',
                    'label': 'SL', 'triggerPrice': float(p.current_sl or p.sl_price),
                    'qty': float(p.initial_qty), 'time': 'Protection Active', 'source': 'virtual'
                })
            
            if p.tp1_price > 0 and f"{p.symbol}_TP1" not in seen_binance_types:
                tp1_orders.append({
                    'orderId': f"virtual_tp1_{p.id}",
                    'symbol': p.symbol, 'side': s_close, 'type': 'VIRTUAL_GUARD',
                    'label': 'TP1', 'triggerPrice': float(p.tp1_price),
                    'qty': float(p.initial_qty) * (float(p.tp1_qty_pct or 50)/100),
                    'time': 'Protection Active', 'source': 'virtual'
                })

        return {"success": True, "tp1_orders": tp1_orders, "tp2_orders": tp2_orders, "sl_orders": sl_orders}
    except Exception as e:
        return {"success": False, "error": str(e), "tp1_orders":[], "tp2_orders":[], "sl_orders":[]}
