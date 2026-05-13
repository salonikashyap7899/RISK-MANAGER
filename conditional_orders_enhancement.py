import logic
from models import TradePosition, db
from datetime import datetime

def get_tp1_and_sl_orders(user_id):
    try:
        client = logic.get_client(user_id)
        if not client:
            return {"success": False, "error": "No client", "tp1_orders": [], "tp2_orders": [], "sl_orders": []}
        
        # 1. Fetch all regular open orders (Limit, Stop Market, etc)
        all_orders = []
        try:
            all_orders = client.futures_get_open_orders(recvWindow=10000)
        except Exception as e:
            print(f"Error fetching open orders: {e}")
            
        # 2. Fetch all conditional algo orders (Trailing stops, Take Profit Market, etc)
        algo_orders = []
        try:
            if hasattr(client, 'futures_get_algo_orders'):
                algo_resp = client.futures_get_algo_orders(recvWindow=10000)
                algo_orders = algo_resp if isinstance(algo_resp, list) else algo_resp.get('orders', [])
            elif hasattr(client, '_request_futures_api'):
                algo_resp = client._request_futures_api('get', 'algoOrder/openOrders', True, data={'recvWindow': 10000})
                algo_orders = algo_resp if isinstance(algo_resp, list) else algo_resp.get('orders', [])
        except Exception as e:
            print(f"Error fetching algo orders: {e}")

        tp1_orders = []
        tp2_orders = []
        sl_orders = []
        
        # Combine all fetched real orders from Binance
        combined_orders = all_orders + algo_orders
        active_binance_symbols = set()

        # 3. Correctly Map Live Binance Orders to Dashboard
        for order in combined_orders:
            order_type = order.get('type', '').upper()
            sym = order.get('symbol')
            active_binance_symbols.add(sym)
            
            # Format the time securely
            raw_time = order.get('time') or order.get('updateTime', 0)
            order_time = datetime.fromtimestamp(int(raw_time) / 1000).strftime('%Y-%m-%d %H:%M:%S') if raw_time else 'N/A'
            
            order_info = {
                'orderId': order.get('orderId') or order.get('algoId'),
                'symbol': sym,
                'side': order.get('side'),
                'type': order_type,
                'triggerPrice': float(order.get('stopPrice') or order.get('price') or 0),
                'qty': float(order.get('origQty') or 0),
                'time': order_time,
                'source': 'binance' # This ensures the dashboard knows it's a real order
            }
            
            # Categorize based on Binance order type
            if 'TAKE_PROFIT' in order_type or order_type == 'LIMIT':
                order_info['label'] = 'TP1'
                tp1_orders.append(order_info)
            elif 'STOP' in order_type:
                order_info['label'] = 'SL'
                sl_orders.append(order_info)

        # 4. Fallback for Virtual Orders (ONLY if Binance has no live order for that symbol yet)
        try:
            open_positions = TradePosition.query.filter_by(user_id=user_id, status='OPEN').all()
            for pos in open_positions:
                sym = pos.symbol
                
                # Skip virtual creation if Binance already returned actual orders for this symbol
                if sym in active_binance_symbols:
                    continue 

                side_close = 'SELL' if pos.side.upper() == 'BUY' else 'BUY'

                # Virtual TP1
                if pos.tp1_price and pos.tp1_price > 0:
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
                
                # Virtual SL
                if pos.sl_price and pos.sl_price > 0:
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
        except Exception as e:
            print(f"Error fetching virtual fallbacks: {e}")

        return {
            "success": True,
            "tp1_orders": tp1_orders,
            "tp2_orders": tp2_orders,
            "sl_orders": sl_orders,
        }
    except Exception as e:
        print(f"Error in get_tp1_and_sl_orders: {e}")
        return {"success": False, "error": str(e), "tp1_orders": [], "tp2_orders": [], "sl_orders": []}
