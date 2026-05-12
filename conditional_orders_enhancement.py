import logic
from models import TradePosition, db
from datetime import datetime

def get_tp1_and_sl_orders(user_id):
    try:
        client = logic.get_client(user_id)
        if not client:
            return {"success": False, "tp1_orders": [], "tp2_orders": [], "sl_orders": []}
        
        # 1. Fetch from standard AND algo books
        all_binance_orders = []
        try:
            # Standard Limit and Stop Market orders
            reg_orders = client.futures_get_open_orders(recvWindow=10000)
            for o in reg_orders:
                o['_api_source'] = 'regular'
                all_binance_orders.append(o)
                
            # Take Profit Market & Trailing orders often live here
            if hasattr(client, 'futures_get_algo_orders'):
                algo_resp = client.futures_get_algo_orders(recvWindow=10000)
                algo_list = algo_resp if isinstance(algo_resp, list) else algo_resp.get('orders', [])
                for o in algo_list:
                    o['_api_source'] = 'algo'
                    all_binance_orders.append(o)
        except Exception as e:
            print(f"⚠️ Fetch Error: {e}")

        tp1_orders, tp2_orders, sl_orders = [], [], []
        active_slots = set() # key format: SYMBOL_LABEL

        for o in all_binance_orders:
            # Capture the correct ID: regular uses orderId, algo uses algoId
            oid = str(o.get('algoId') or o.get('orderId') or '')
            if not oid: continue
            
            symbol = o.get('symbol', '')
            o_type = (o.get('type') or o.get('algoType') or '').upper()
            trigger = float(o.get('stopPrice') or o.get('triggerPrice') or o.get('price') or 0)
            qty = float(o.get('origQty') or o.get('qty') or 0)
            
            # Classification logic
            label = "ORDER"
            if 'TAKE_PROFIT' in o_type:
                label, list_ref = 'TP1', tp1_orders
            elif 'STOP' in o_type or 'TRAILING' in o_type:
                label, list_ref = 'SL', sl_orders
            elif o_type == 'LIMIT' and (o.get('reduceOnly') or o.get('closePosition')):
                label, list_ref = 'TP2', tp2_orders
            else: continue

            active_slots.add(f"{symbol}_{label}")
            
            list_ref.append({
                'orderId': oid,
                'symbol': symbol,
                'side': o.get('side', '').upper(),
                'type': o_type,
                'label': label,
                'triggerPrice': trigger,
                'qty': qty,
                'time': 'Binance Live',
                'source': o['_api_source']
            })

        # 2. Virtual Protection (Dashboard Guard) - only add if Binance slot is empty
        db_positions = TradePosition.query.filter_by(user_id=user_id, status='open').all()
        for pos in db_positions:
            side_close = 'SELL' if pos.side == 'LONG' else 'BUY'
            
            # Virtual SL
            if pos.sl_price > 0 and f"{pos.symbol}_SL" not in active_slots:
                sl_orders.append({
                    'orderId': f"virtual_sl_{pos.id}", 'symbol': pos.symbol, 'side': side_close,
                    'type': 'GUARD', 'label': 'SL', 'triggerPrice': float(pos.current_sl or pos.sl_price),
                    'qty': float(pos.initial_qty), 'time': 'Protection Mode', 'source': 'virtual'
                })
            
            # Virtual TP1
            if pos.tp1_price > 0 and f"{pos.symbol}_TP1" not in active_slots:
                tp1_orders.append({
                    'orderId': f"virtual_tp1_{pos.id}", 'symbol': pos.symbol, 'side': side_close,
                    'type': 'GUARD', 'label': 'TP1', 'triggerPrice': float(pos.tp1_price),
                    'qty': float(pos.initial_qty) * 0.5, 'time': 'Protection Mode', 'source': 'virtual'
                })

        return {"success": True, "tp1_orders": tp1_orders, "tp2_orders": tp2_orders, "sl_orders": sl_orders}
    except Exception as e:
        print(f"❌ Critical fetch fail: {e}")
        return {"success": False, "error": str(e), "tp1_orders":[], "sl_orders":[], "tp2_orders":[]}
