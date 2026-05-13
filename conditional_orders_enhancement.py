import logic
from models import TradePosition, db
from datetime import datetime

def get_tp1_and_sl_orders(user_id):
    try:
        client = logic.get_client(user_id)
        if not client:
            return {"success": False, "error": "No client", "tp1_orders": [], "tp2_orders": [], "sl_orders":[]}
        
        # 1. Fetch regular open orders from Binance
        all_orders = []
        try:
            all_orders = client.futures_get_open_orders(recvWindow=10000)
        except Exception as e:
            print(f"Error fetching open orders: {e}")
            
        # 2. Fetch conditional algo orders from Binance
        algo_orders = []
        try:
            if hasattr(client, 'futures_get_algo_orders'):
                algo_resp = client.futures_get_algo_orders(recvWindow=10000)
                algo_orders = algo_resp if isinstance(algo_resp, list) else algo_resp.get('orders',[])
            elif hasattr(client, '_request_futures_api'):
                algo_resp = client._request_futures_api('get', 'algoOrder/openOrders', True, data={'recvWindow': 10000})
                algo_orders = algo_resp if isinstance(algo_resp, list) else algo_resp.get('orders',[])
        except Exception as e:
            print(f"Error fetching algo orders: {e}")

        # 3. DEDUPLICATE ORDERS (Fix for the double-counting bug)
        unique_orders_map = {}
        for o in all_orders:
            oid = o.get('orderId')
            if oid:
                unique_orders_map[str(oid)] = o

        for o in algo_orders:
            # Algo orders sometimes use 'algoId' if 'orderId' is 0 or missing
            oid = o.get('orderId')
            if not oid or oid == 0:
                oid = o.get('algoId')
            if oid:
                unique_orders_map[str(oid)] = o
        
        # Convert the unique dictionary back to a list
        combined_orders = list(unique_orders_map.values())

        tp1_orders = []
        tp2_orders = []
        sl_orders = []

        # Group real orders by symbol for easy matching
        real_orders_by_symbol = {}
        for order in combined_orders:
            sym = order.get('symbol')
            if sym not in real_orders_by_symbol:
                real_orders_by_symbol[sym] = []
            real_orders_by_symbol[sym].append(order)

        # Get ONLY OPEN DB positions to provide context and virtual fallbacks
        active_positions = TradePosition.query.filter_by(user_id=user_id, status='open').all()

        for pos in active_positions:
            sym = pos.symbol
            # The order side needed to close this position
            side_close = 'BUY' if pos.side.upper() == 'SELL' else 'SELL'
            
            pos_real_orders = real_orders_by_symbol.get(sym, [])
            
            has_real_tp = False
            has_real_sl = False

            # 4. Process REAL Binance Orders
            for o in pos_real_orders:
                o_type = o.get('type', '')
                o_side = o.get('side', '')
                
                # Match orders that are intended to close the position
                if o_side == side_close:
                    trigger_price = float(o.get('stopPrice', 0) or o.get('price', 0))
                    qty = float(o.get('origQty', 0))
                    
                    order_id = o.get('orderId')
                    if not order_id or order_id == 0:
                        order_id = o.get('algoId')

                    formatted_order = {
                        'orderId': order_id,
                        'symbol': sym,
                        'side': o_side,
                        'type': o_type,
                        'triggerPrice': trigger_price,
                        'qty': qty,
                        'time': datetime.fromtimestamp(o.get('time', o.get('updateTime', 0)) / 1000.0).strftime('%Y-%m-%d %H:%M:%S') if o.get('time', o.get('updateTime')) else 'N/A',
                        'source': 'binance_real'
                    }

                    # Classify the real order
                    if o_type in ['TAKE_PROFIT_MARKET', 'TAKE_PROFIT', 'LIMIT']:
                        formatted_order['label'] = 'TP'
                        tp1_orders.append(formatted_order)
                        has_real_tp = True
                    elif o_type in ['STOP_MARKET', 'STOP']:
                        formatted_order['label'] = 'SL'
                        sl_orders.append(formatted_order)
                        has_real_sl = True

            # 5. Fallback to Virtual DB Orders ONLY if Real Orders are missing
            if not has_real_tp and pos.tp1_price and pos.tp1_price > 0:
                tp1_qty = float(pos.initial_qty) * (float(pos.tp1_qty_pct) / 100.0) if pos.tp1_qty_pct else float(pos.initial_qty)
                tp1_orders.append({
                    'orderId': f"virtual_tp1_{pos.id}",
                    'symbol': sym,
                    'side': side_close,
                    'type': 'VIRTUAL_LIMIT',
                    'label': 'TP1',
                    'triggerPrice': float(pos.tp1_price),
                    'qty': tp1_qty,
                    'time': pos.updated_at.strftime('%Y-%m-%d %H:%M:%S') if pos.updated_at else 'N/A',
                    'source': 'virtual'
                })

            if not has_real_sl and pos.sl_price and pos.sl_price > 0:
                sl_orders.append({
                    'orderId': f"virtual_sl_{pos.id}",
                    'symbol': sym,
                    'side': side_close,
                    'type': 'VIRTUAL_STOP',
                    'label': 'SL',
                    'triggerPrice': float(pos.sl_price),
                    'qty': float(pos.initial_qty),
                    'time': pos.updated_at.strftime('%Y-%m-%d %H:%M:%S') if pos.updated_at else 'N/A',
                    'source': 'virtual'
                })
                
            # Virtual TP2 logic (Restored from your original file)
            if pos.tp2_price and pos.tp2_price > 0 and not any(o['symbol'] == sym for o in tp2_orders):
                tp1_qty = float(pos.initial_qty) * (float(pos.tp1_qty_pct) / 100.0) if pos.tp1_qty_pct else 0
                tp2_qty = float(pos.initial_qty) - tp1_qty
                if tp2_qty > 0:
                    tp2_orders.append({
                        'orderId': f"virtual_tp2_{pos.id}",
                        'symbol': sym,
                        'side': side_close,
                        'type': 'VIRTUAL_LIMIT',
                        'label': 'TP2',
                        'triggerPrice': float(pos.tp2_price),
                        'qty': tp2_qty,
                        'time': pos.updated_at.strftime('%Y-%m-%d %H:%M:%S') if pos.updated_at else 'N/A',
                        'source': 'virtual'
                    })

        return {
            "success": True,
            "tp1_orders": tp1_orders,
            "tp2_orders": tp2_orders,
            "sl_orders": sl_orders,
        }
    except Exception as e:
        print(f"[ERROR] get_tp1_and_sl_orders: {e}")
        return {"success": False, "error": str(e), "tp1_orders": [], "tp2_orders": [], "sl_orders": []}
