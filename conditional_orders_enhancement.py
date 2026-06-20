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

        # Fallback 1: direct futures_get_open_orders fetch
        if not tp1_orders and not sl_orders:
            client = logic.get_client(user_id)
            if client:
                try:
                    raw = client.futures_get_open_orders(recvWindow=10000)
                    for o in raw:
                        o_type = o.get('type', '').upper()
                        has_stop = float(o.get('stopPrice', 0)) > 0
                        if o_type in ['STOP', 'STOP_MARKET', 'TAKE_PROFIT', 'TAKE_PROFIT_MARKET', 'TRAILING_STOP_MARKET', 'LIMIT', 'LIMIT_MAKER', 'TRAILING_STOP_MARKET_ALGO'] or has_stop:
                            symbol = o.get('symbol', '')
                            db_pos = pos_map.get(symbol)
                            label = 'SL'
                            if 'TAKE_PROFIT' in o_type: 
                                label = 'TP1'
                            elif o_type in ['LIMIT', 'LIMIT_MAKER'] and o.get('reduceOnly'):
                                label = 'TP2'
                            
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
                                'position_entry': float(db_pos.entry_price) if db_pos and db_pos.entry_price else None,
                                'position_sl': float(db_pos.sl_price) if db_pos and db_pos.sl_price else None,
                                'position_tp1': float(db_pos.tp1_price) if db_pos and db_pos.tp1_price else None,
                                'position_status': db_pos.status if db_pos else 'unknown',
                            }
                            if label == 'TP1': tp1_orders.append(context)
                            elif label == 'TP2': tp2_orders.append(context)
                            else: sl_orders.append(context)
                except Exception as fe:
                    print(f"[fallback] direct fetch failed: {fe}")

        # Fallback 2: papi endpoint (Portfolio Margin accounts)
        if not tp1_orders and not sl_orders:
            try:
                papi_client = logic.get_client(user_id)
                if not papi_client:
                    raise Exception("No client available for papi fallback")
                papi_raw = logic._fetch_papi(papi_client, '/papi/v1/um/openOrders', {'recvWindow': 10000})
                if papi_raw and isinstance(papi_raw, list):
                    for o in papi_raw:
                        o_type = o.get('type', '').upper()
                        has_stop = float(o.get('stopPrice', 0)) > 0
                        if o_type in ['STOP', 'STOP_MARKET', 'TAKE_PROFIT', 'TAKE_PROFIT_MARKET', 'TRAILING_STOP_MARKET', 'LIMIT', 'LIMIT_MAKER', 'TRAILING_STOP_MARKET_ALGO'] or has_stop:
                            symbol = o.get('symbol', '')
                            db_pos = pos_map.get(symbol)
                            label = 'SL'
                            if 'TAKE_PROFIT' in o_type:
                                label = 'TP1'
                            elif o_type in ['LIMIT', 'LIMIT_MAKER'] and o.get('reduceOnly'):
                                label = 'TP2'
                            context = {
                                'orderId': o.get('orderId'),
                                'symbol': symbol,
                                'side': o.get('side'),
                                'type': o_type,
                                'label': label,
                                'triggerPrice': float(o.get('stopPrice', 0)),
                                'price': float(o.get('price', 0)),
                                'qty': float(o.get('origQty', 0)),
                                'time': 'PAPI-Fetch',
                                'source': 'papi',
                                'position_entry': float(db_pos.entry_price) if db_pos and db_pos.entry_price else None,
                                'position_sl': float(db_pos.sl_price) if db_pos and db_pos.sl_price else None,
                                'position_tp1': float(db_pos.tp1_price) if db_pos and db_pos.tp1_price else None,
                                'position_status': db_pos.status if db_pos else 'unknown',
                            }
                            if label == 'TP1':
                                tp1_orders.append(context)
                            elif label == 'TP2':
                                tp2_orders.append(context)
                            else:
                                sl_orders.append(context)
            except Exception as papi_fe:
                print(f"[fallback] papi direct fetch failed: {papi_fe}")

        # Fallback 3: direct algo orders fetch (TP1 lives here on most Binance accounts)
        if not tp1_orders:
            try:
                algo_client = logic.get_client(user_id)
                if algo_client:
                    algo_orders = []
                    if hasattr(algo_client, 'futures_get_algo_orders'):
                        try:
                            resp = algo_client.futures_get_algo_orders(recvWindow=10000)
                            if resp:
                                algo_orders = resp if isinstance(resp, list) else resp.get('orders', [])
                        except Exception as ae1:
                            print(f"[fallback-algo] futures_get_algo_orders failed: {ae1}")
                    if not algo_orders and hasattr(algo_client, '_request_futures_api'):
                        try:
                            resp = algo_client._request_futures_api('get', 'algoOrder/openOrders', True, data={'recvWindow': 10000})
                            if resp:
                                algo_orders = resp if isinstance(resp, list) else resp.get('orders', [])
                        except Exception as ae2:
                            print(f"[fallback-algo] _request_futures_api algo failed: {ae2}")
                    for o in algo_orders:
                        o_type = (o.get('type') or o.get('algoType') or '').upper()
                        symbol = o.get('symbol', '')
                        db_pos = pos_map.get(symbol)
                        label = 'TP1' if 'TAKE_PROFIT' in o_type else ('Trail SL' if 'TRAILING' in o_type else 'SL')
                        trigger_price = float(o.get('triggerPrice') or o.get('stopPrice') or 0)
                        context = {
                            'orderId': str(o.get('algoId') or o.get('orderId') or ''),
                            'symbol': symbol,
                            'side': o.get('side'),
                            'type': o_type,
                            'label': label,
                            'triggerPrice': trigger_price,
                            'price': float(o.get('price') or 0),
                            'qty': float(o.get('qty') or o.get('origQty') or 0),
                            'time': 'Algo-Fetch',
                            'source': 'algo',
                            'position_entry': float(db_pos.entry_price) if db_pos and db_pos.entry_price else None,
                            'position_sl': float(db_pos.sl_price) if db_pos and db_pos.sl_price else None,
                            'position_tp1': float(db_pos.tp1_price) if db_pos and db_pos.tp1_price else None,
                            'position_status': db_pos.status if db_pos else 'unknown',
                        }
                        if label == 'TP1':
                            tp1_orders.append(context)
                        elif label in ('Trail SL', 'SL'):
                            sl_orders.append(context)
            except Exception as algo_fe:
                print(f"[fallback-algo] direct algo fetch failed: {algo_fe}")

        return {"success": True, "tp1_orders": tp1_orders, "tp2_orders": tp2_orders, "sl_orders": sl_orders}
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"success": False, "error": str(e), "tp1_orders": [], "tp2_orders": [], "sl_orders": []}
