import logic
from models import TradePosition, db
from datetime import datetime

def get_tp1_and_sl_orders(user_id):
    try:
        client = logic.get_client(user_id)
        if not client:
            return {"success": False, "error": "No client", "tp1_orders": [], "tp2_orders": [], "sl_orders":[]}
        
        all_orders = []
        algo_orders = []
        try:
            all_orders = client.futures_get_open_orders(recvWindow=10000)
        except: pass
            
        try:
            # Fetch algo orders (Where Take Profit Market orders often live)
            if hasattr(client, 'futures_get_algo_orders'):
                algo_resp = client.futures_get_algo_orders(recvWindow=10000)
                algo_orders = algo_resp if isinstance(algo_resp, list) else algo_resp.get('orders',[])
            elif hasattr(client, '_request_futures_api'):
                algo_resp = client._request_futures_api('get', 'algoOrder/openOrders', True, data={'recvWindow': 10000})
                algo_orders = algo_resp if isinstance(algo_resp, list) else algo_resp.get('orders',[])
        except: pass

        db_positions = TradePosition.query.filter_by(user_id=user_id, status='open').all()
        pos_map = {p.symbol: p for p in db_positions}

        tp1_orders = []
        tp2_orders = []
        sl_orders = []
        seen_symbols_types = set() # To prevent showing virtual if real exists

        def _add_order(o, source):
            oid = str(o.get('orderId') or o.get('algoId') or '')
            symbol = o.get('symbol', '')
            o_type = (o.get('type') or o.get('algoType') or '').upper()
            side = o.get('side', '').upper()
            
            trigger = float(o.get('stopPrice') or o.get('triggerPrice') or o.get('price') or 0)
            qty = float(o.get('origQty') or o.get('qty') or 0)
            
            # Classification
            label = "ORDER"
            if 'TAKE_PROFIT' in o_type:
                label = 'TP1'
                tp_list = tp1_orders
            elif 'STOP' in o_type or 'TRAILING' in o_type:
                label = 'SL'
                tp_list = sl_orders
            elif o_type == 'LIMIT' and (o.get('reduceOnly') or o.get('closePosition')):
                label = 'TP2'
                tp_list = tp2_orders
            else:
                return # Skip non-closing orders

            seen_symbols_types.add(f"{symbol}_{label}")

            tp_list.append({
                'orderId': oid,
                'symbol': symbol,
                'side': side,
                'type': o_type,
                'label': label,
                'triggerPrice': trigger,
                'qty': qty,
                'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'source': 'binance'
            })

        # Process real Binance orders first
        for o in all_orders: _add_order(o, 'regular')
        for o in algo_orders: _add_order(o, 'algo')

        # Only add Virtual orders if a real one doesn't exist for that symbol/type
        for sym, pos in pos_map.items():
            side_close = 'SELL' if pos.side == 'LONG' else 'BUY'
            
            # Virtual SL
            if pos.sl_price > 0 and f"{sym}_SL" not in seen_symbols_types:
                sl_orders.append({
                    'orderId': f"virtual_sl_{pos.id}",
                    'symbol': sym,
                    'side': side_close,
                    'type': 'VIRTUAL_STOP',
                    'label': 'SL',
                    'triggerPrice': float(pos.current_sl or pos.sl_price),
                    'qty': float(pos.initial_qty),
                    'time': 'Dashboard Guard',
                    'source': 'virtual'
                })
            
            # Virtual TP1
            if pos.tp1_price > 0 and f"{sym}_TP1" not in seen_symbols_types:
                tp1_orders.append({
                    'orderId': f"virtual_tp1_{pos.id}",
                    'symbol': sym,
                    'side': side_close,
                    'type': 'VIRTUAL_TP',
                    'label': 'TP1',
                    'triggerPrice': float(pos.tp1_price),
                    'qty': float(pos.initial_qty) * (float(pos.tp1_qty_pct or 100) / 100.0),
                    'time': 'Dashboard Guard',
                    'source': 'virtual'
                })

        return {"success": True, "tp1_orders": tp1_orders, "tp2_orders": tp2_orders, "sl_orders": sl_orders}
    except Exception as e:
        return {"success": False, "error": str(e), "tp1_orders": [], "tp2_orders":[], "sl_orders":[]}
