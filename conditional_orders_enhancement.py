import logic
from models import TradePosition, db
from datetime import datetime

def get_tp1_and_sl_orders(user_id):
    try:
        client = logic.get_client(user_id)
        if not client:
            return {"success": False, "error": "No connection", "tp1_orders": [], "tp2_orders": [], "sl_orders": []}
        
        # 1. Fetch EVERYTHING from both Regular and Algo Order books
        all_binance_orders = []
        try:
            # Regular Trigger orders (Standard Stop/TP)
            regs = client.futures_get_open_orders(recvWindow=10000)
            for o in regs:
                o['_source_type'] = 'regular'
                all_binance_orders.append(o)
                
            # Algo Trigger orders (Specifically for Take Profit Market / Stop Market)
            algo_resp = None
            if hasattr(client, 'futures_get_algo_orders'):
                algo_resp = client.futures_get_algo_orders(recvWindow=10000)
            elif hasattr(client, '_request_futures_api'):
                # Fallback for library versions that don't have the explicit method
                algo_resp = client._request_futures_api('get', 'algoOrder/openOrders', True, data={'recvWindow': 10000})
            
            if algo_resp:
                algos = algo_resp if isinstance(algo_resp, list) else algo_resp.get('orders', [])
                for o in algos:
                    o['_source_type'] = 'algo'
                    all_binance_orders.append(o)
        except Exception as fetch_err:
            print(f"Fetch Warning: {fetch_err}")

        tp1_orders, tp2_orders, sl_orders = [], [], []
        active_slots = set() # format: Symbol_Label

        # 2. Categorize all orders across all symbols
        for o in all_binance_orders:
            # Capture correctly (Algos use 'algoId', Regs use 'orderId')
            oid = str(o.get('algoId') or o.get('orderId') or '')
            if not oid: continue
            
            sym = o.get('symbol', '')
            raw_type = (o.get('type') or o.get('algoType') or '').upper()
            price = float(o.get('stopPrice') or o.get('triggerPrice') or o.get('price') or 0)
            qty = float(o.get('origQty') or o.get('qty') or 0)
            side = o.get('side', '').upper()
            
            # Map the exact Binance App terminology
            label = ""
            if 'TAKE_PROFIT' in raw_type:
                label, target_list = "TP1", tp1_orders
            elif 'STOP' in raw_type or 'TRAILING' in raw_type:
                label, target_list = "SL", sl_orders
            elif raw_type == 'LIMIT' and (o.get('reduceOnly') or o.get('closePosition')):
                label, target_list = "TP2", tp2_orders
            else:
                continue # Skip non-conditional entry orders

            active_slots.add(f"{sym}_{label}")
            
            target_list.append({
                'orderId': oid,
                'symbol': sym,
                'side': side,
                'type': raw_type.replace('_', ' '),
                'label': label,
                'triggerPrice': price,
                'qty': qty,
                'time': 'Binance Account', # We pull this live from the API
                'source': o['_source_type']
            })

        # 3. Add "Virtual Guard" placeholders only for symbols that have NO real Binance order
        db_pos = TradePosition.query.filter_by(user_id=user_id, status='open').all()
        for p in db_pos:
            close_side = 'SELL' if p.side == 'LONG' else 'BUY'
            
            if p.sl_price > 0 and f"{p.symbol}_SL" not in active_slots:
                sl_orders.append({'orderId': f"virtual_sl_{p.id}", 'symbol': p.symbol, 'side': close_side, 'type': 'GUARD', 'label': 'SL', 'triggerPrice': float(p.current_sl or p.sl_price), 'qty': float(p.initial_qty), 'time': 'Protection Mode', 'source': 'virtual'})
            
            if p.tp1_price > 0 and f"{p.symbol}_TP1" not in active_slots:
                tp1_orders.append({'orderId': f"virtual_tp1_{p.id}", 'symbol': p.symbol, 'side': close_side, 'type': 'GUARD', 'label': 'TP1', 'triggerPrice': float(p.tp1_price), 'qty': float(p.initial_qty) * 0.5, 'time': 'Protection Mode', 'source': 'virtual'})

        return {"success": True, "tp1_orders": tp1_orders, "tp2_orders": tp2_orders, "sl_orders": sl_orders}
    except Exception as e:
        return {"success": False, "error": str(e), "tp1_orders": [], "tp2_orders": [], "sl_orders": []}
