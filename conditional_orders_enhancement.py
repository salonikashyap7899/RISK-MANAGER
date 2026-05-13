import logic
from models import TradePosition, db
from datetime import datetime
import time

# Per-user result cache for get_tp1_and_sl_orders
# Prevents repeated Binance calls on every frontend poll
_tp1_sl_cache = {}           # {user_id: (ts_seconds, result_dict)}
TP1_SL_CACHE_DURATION = 30  # seconds — matches CONDITIONAL_CACHE_MS in logic.py


def get_tp1_and_sl_orders(user_id):
    global _tp1_sl_cache

    now = time.time()
    now_ms = int(now * 1000)

    # Honour the global IP ban — return cached data immediately if banned
    if now_ms < logic._api_ban_until_ms:
        cached = _tp1_sl_cache.get(user_id)
        if cached:
            print(f"⛔ IP ban active — returning cached TP/SL orders for user {user_id}")
            return cached[1]
        return {"success": False, "error": "IP temporarily banned by Binance — please wait",
                "tp1_orders": [], "tp2_orders": [], "sl_orders": []}

    # Serve from local cache if fresh
    cached = _tp1_sl_cache.get(user_id)
    if cached and (now - cached[0]) < TP1_SL_CACHE_DURATION:
        return cached[1]

    try:
        client = logic.get_client(user_id)
        if not client:
            return {"success": False, "error": "No client", "tp1_orders": [], "tp2_orders": [], "sl_orders": []}

        # Fetch all regular open orders (Limit, Stop Market, etc)
        all_orders = []
        try:
            all_orders = client.futures_get_open_orders(recvWindow=10000)
        except Exception as e:
            err_str = str(e)
            print(f"Error fetching open orders: {e}")
            # Detect -1003 IP ban and propagate to global tracker
            if "-1003" in err_str and "banned until" in err_str:
                try:
                    ban_ts = int(err_str.split("banned until")[1].split(".")[0].strip())
                    logic._api_ban_until_ms = ban_ts
                    logic._conditional_ban_until = ban_ts
                    print(f"⛔ IP ban detected in TP1/SL fetch — blocked until {ban_ts}ms")
                except Exception:
                    logic._api_ban_until_ms = now_ms + 120_000
            # Return cached result if available, else empty
            if cached:
                return cached[1]
            return {"success": False, "error": err_str, "tp1_orders": [], "tp2_orders": [], "sl_orders": []}

        # Fetch all conditional algo orders (Trailing stops, Take Profit Market, etc)
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

        # Get ONLY OPEN DB positions to provide context and virtual guard fallbacks
        db_positions = (
            TradePosition.query
            .filter_by(user_id=user_id, status='open')
            .order_by(TradePosition.created_at.desc())
            .all()
        )

        pos_map = {}
        for p in db_positions:
            if p.symbol not in pos_map:
                pos_map[p.symbol] = p

        tp1_orders = []
        tp2_orders = []
        sl_orders = []
        seen_ids = set()

        def _add_order(o, source):
            oid = str(o.get('orderId') or o.get('algoId') or '')
            if not oid or oid in seen_ids:
                return
            seen_ids.add(oid)

            o_type = (o.get('type') or o.get('algoType') or '').upper()
            symbol = o.get('symbol', '')
            side = o.get('side', '').upper()
            trigger = float(o.get('stopPrice') or o.get('triggerPrice') or o.get('price') or 0)
            qty = float(o.get('origQty') or o.get('qty') or 0)
            book_time = o.get('time') or o.get('bookTime') or o.get('updateTime') or 0

            if book_time:
                time_str = datetime.fromtimestamp(int(book_time) / 1000).strftime('%Y-%m-%d %H:%M:%S')
            else:
                time_str = 'N/A'

            db_pos = pos_map.get(symbol)

            context = {
                'orderId': oid,
                'symbol': symbol,
                'side': side,
                'type': o_type,
                'triggerPrice': trigger,
                'qty': qty,
                'time': time_str,
                'source': source,
                'position_entry': float(db_pos.entry_price) if db_pos and db_pos.entry_price else None,
                'position_sl': float(db_pos.sl_price) if db_pos and db_pos.sl_price else None,
                'position_tp1': float(db_pos.tp1_price) if db_pos and db_pos.tp1_price else None,
                'position_status': db_pos.status if db_pos else 'unknown',
            }

            # Classification Logic
            # 1. TP1: Take profit market
            if 'TAKE_PROFIT' in o_type and 'LIMIT' not in o_type:
                context['label'] = 'TP1'
                tp1_orders.append(context)
            # 2. TP2: Limit order (only closing positions)
            elif o_type == 'LIMIT':
                if o.get('reduceOnly') or o.get('closePosition'):
                    context['label'] = 'TP2'
                    tp2_orders.append(context)
            # 3. SL: Stop market or trailing stop
            elif 'STOP' in o_type or 'TRAILING' in o_type:
                context['label'] = 'Trail SL' if 'TRAILING' in o_type else 'SL'
                sl_orders.append(context)

        # Map all real Binance orders
        for o in all_orders:
            _add_order(o, 'regular')
        for o in algo_orders:
            _add_order(o, 'algo')

        # INJECT VIRTUAL ORDERS for positions that failed to place on Binance (e.g. < 5 USDT Min Notional)
        for sym, pos in pos_map.items():
            side_close = 'SELL' if pos.side == 'LONG' else 'BUY'

            # Virtual SL
            if pos.sl_price and pos.sl_price > 0 and not any(o['symbol'] == sym for o in sl_orders):
                sl_orders.append({
                    'orderId': f"virtual_sl_{pos.id}",
                    'symbol': sym,
                    'side': side_close,
                    'type': 'VIRTUAL_STOP',
                    'label': 'SL',
                    'triggerPrice': float(pos.current_sl or pos.sl_price),
                    'qty': float(pos.initial_qty),
                    'time': pos.updated_at.strftime('%Y-%m-%d %H:%M:%S') if pos.updated_at else 'N/A',
                    'source': 'virtual'
                })

            # Virtual TP1
            if pos.tp1_price and pos.tp1_price > 0 and not any(o['symbol'] == sym for o in tp1_orders):
                tp1_qty = float(pos.initial_qty) * (float(pos.tp1_qty_pct) / 100.0) if pos.tp1_qty_pct else float(pos.initial_qty)
                tp1_orders.append({
                    'orderId': f"virtual_tp1_{pos.id}",
                    'symbol': sym,
                    'side': side_close,
                    'type': 'VIRTUAL_TP',
                    'label': 'TP1',
                    'triggerPrice': float(pos.tp1_price),
                    'qty': tp1_qty,
                    'time': pos.updated_at.strftime('%Y-%m-%d %H:%M:%S') if pos.updated_at else 'N/A',
                    'source': 'virtual'
                })

            # Virtual TP2
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

        result = {
            "success": True,
            "tp1_orders": tp1_orders,
            "tp2_orders": tp2_orders,
            "sl_orders": sl_orders,
        }

        # Store in cache so repeated polls don't hammer Binance
        _tp1_sl_cache[user_id] = (now, result)
        return result

    except Exception as e:
        print(f"[ERROR] get_tp1_and_sl_orders: {e}")
        # Return cached result on error if available
        if cached:
            return cached[1]
        return {
            "success": False,
            "error": str(e),
            "tp1_orders": [],
            "tp2_orders": [],
            "sl_orders": [],
        }
