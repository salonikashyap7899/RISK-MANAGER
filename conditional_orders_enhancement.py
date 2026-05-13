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

        # 3. BULLETPROOF DEDUPLICATION (Order Fingerprinting)
        # Identifies duplicates by actual order parameters rather than relying on Binance's inconsistent IDs
        unique_orders_map = {}
        raw_combined = all_orders + algo_orders
        
        for o in raw_combined:
            sym = o.get('symbol')
            side = o.get('side')
            o_type = o.get('type')
            price = float(o.get('stopPrice', 0) or o.get('price', 0))
            qty = float(o.get('origQty', 0))
            
            # Create a unique fingerprint: Symbol + Side + Type + Price + Quantity
            fingerprint = f"{sym}_{side}_{o_type}_{price}_{qty}"
            
            # Prefer the version that has a valid orderId if both exist
            if fingerprint not in unique_orders_map:
                unique_orders_map[fingerprint] = o
            else:
                existing_oid = unique_orders_map[fingerprint].get('orderId', 0)
                new_oid = o.get('orderId', 0)
                if new_oid and new_oid != 0 and (not existing_oid or existing_oid == 0):
                    unique_orders_map[fingerprint] = o

        combined_orders = list(unique_orders_map.values())

        # 4. GET DB POSITIONS ONCE (Prevents UI duplication if you have multiple DB rows for one coin)
        active_positions = TradePosition.query.filter_by(user_id=user_id, status='open').all()
        positions_map = {}
        for pos in active_positions:
            if pos.symbol not in positions_map:
                positions_map[pos.symbol] = {
                    'side_close': 'BUY' if pos.side.upper() == 'SELL' else 'SELL',
                    'pos': pos
                }

        tp1_orders = []
        tp2_orders = []
        sl_orders = []
        
        real_tp_symbols = set()
        real_sl_symbols = set()

        # 5. PROCESS REAL ORDERS EXACTLY ONCE
        for o in combined_orders:
            sym = o.get('symbol')
            o_side = o.get('side')
            o_type = o.get('type', '')

            # Only process if this order matches the closing direction of an active DB position
            if sym in positions_map and o_side == positions_map[sym]['side_close']:
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

                if o_type in ['TAKE_PROFIT_MARKET', 'TAKE_PROFIT', 'LIMIT']:
                    formatted_order['label'] = 'TP'
                    tp1_orders.append(formatted_order)
                    real_tp_symbols.add(sym)
                elif o_type in ['STOP_MARKET', 'STOP']:
                    formatted_order['label'] = 'SL'
                    sl_orders.append(formatted_order)
                    real_sl_symbols.add(sym)

        # 6. ADD VIRTUAL FALLBACKS (Only for coins where real orders are entirely missing)
        for sym, data in positions_map.items():
            pos = data['pos']
            side_close = data['side_close']
            
            if sym not in real_tp_symbols and pos.tp1_price and pos.tp1_price > 0:
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

            if sym not in real_sl_symbols and pos.sl_price and pos.sl_price > 0:
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
                
            # Virtual TP2
            if pos.tp2_price and pos.tp2_price > 0:
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
