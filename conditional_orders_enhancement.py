import logic
from models import TradePosition, db
from datetime import datetime
import time

# Per-user result cache
_tp1_sl_cache = {}
TP1_SL_CACHE_DURATION = 5   # seconds — match the 5-second frontend poll interval


def _fetch_algo_orders(client):
    """
    Fetch Binance Futures algo open orders.
    The correct Binance endpoint is /fapi/v1/algo/openOrders  (NOT algoOrder/openOrders).
    python-binance 1.0.x exposes no direct method, so we use the raw request helper.
    """
    algo_orders = []
    try:
        resp = client._request_futures_api('get', 'algo/openOrders', True,
                                           data={'recvWindow': 10000})
        if isinstance(resp, list):
            algo_orders = resp
        elif isinstance(resp, dict):
            algo_orders = resp.get('orders', []) or resp.get('algoOrders', [])
        print(f"[TP/SL] Algo orders fetched: {len(algo_orders)}")
    except Exception as e:
        print(f"[TP/SL] Algo orders fetch failed (non-critical): {e}")
    return algo_orders


def _fetch_position_tpsl(client):
    """
    Fetch position-level TP/SL that Binance stores on the position itself
    (set via the Binance app 'TP/SL' button on a position, not as separate orders).
    These appear in /fapi/v3/positionRisk as stopPrice / takeProfitPrice fields.
    Falls back to v2 if v3 is unavailable.
    Returns list of position dicts that have non-zero stopPrice or takeProfitPrice.
    """
    positions = []
    # Try v3 endpoint first (has stopPrice + takeProfitPrice)
    try:
        resp = client._request_futures_api('get', 'positionRisk', True,
                                           version=3,
                                           data={'recvWindow': 10000})
        if isinstance(resp, list):
            positions = resp
        print(f"[TP/SL] Got {len(positions)} positions from v3 positionRisk")
    except Exception as e3:
        print(f"[TP/SL] v3 positionRisk failed, trying v2: {e3}")
        try:
            positions = client.futures_position_information(recvWindow=10000) or []
            print(f"[TP/SL] Got {len(positions)} positions from v2 positionRisk")
        except Exception as e2:
            print(f"[TP/SL] Both position risk endpoints failed: {e2}")

    # Filter to only positions that are open and have TP/SL values
    result = []
    for p in positions:
        try:
            amt = float(p.get('positionAmt', 0) or 0)
            if abs(amt) == 0:
                continue
            stop_price = float(p.get('stopPrice', 0) or 0)
            tp_price   = float(p.get('takeProfitPrice', 0) or 0)
            if stop_price > 0 or tp_price > 0:
                result.append({
                    'symbol':          p.get('symbol', ''),
                    'positionAmt':     amt,
                    'stopPrice':       stop_price,
                    'takeProfitPrice': tp_price,
                    'entryPrice':      float(p.get('entryPrice', 0) or 0),
                    'markPrice':       float(p.get('markPrice', 0) or 0),
                })
        except Exception:
            continue
    print(f"[TP/SL] Positions with embedded TP/SL: {len(result)}")
    return result


def get_tp1_and_sl_orders(user_id):
    global _tp1_sl_cache

    now    = time.time()
    now_ms = int(now * 1000)

    # Honour global IP ban
    if now_ms < logic._api_ban_until_ms:
        cached = _tp1_sl_cache.get(user_id)
        if cached:
            print(f"[TP/SL] IP ban active — returning cached data for user {user_id}")
            return cached[1]
        return {"success": False,
                "error": "IP temporarily banned by Binance — please wait",
                "tp1_orders": [], "tp2_orders": [], "sl_orders": []}

    # Serve from local cache if still fresh
    cached = _tp1_sl_cache.get(user_id)
    if cached and (now - cached[0]) < TP1_SL_CACHE_DURATION:
        return cached[1]

    try:
        client = logic.get_client(user_id)
        if not client:
            return {"success": False, "error": "No Binance client",
                    "tp1_orders": [], "tp2_orders": [], "sl_orders": []}

        # ── 1. Regular open orders (covers STOP_MARKET, TAKE_PROFIT_MARKET, etc.) ──
        all_orders = []
        try:
            all_orders = client.futures_get_open_orders(recvWindow=10000)
            print(f"[TP/SL] Regular open orders: {len(all_orders)}")
        except Exception as e:
            err_str = str(e)
            print(f"[TP/SL] futures_get_open_orders failed: {err_str}")
            if "-1003" in err_str and "banned until" in err_str:
                try:
                    ban_ts = int(err_str.split("banned until")[1].split(".")[0].strip())
                    logic._api_ban_until_ms = ban_ts
                    logic._conditional_ban_until = ban_ts
                except Exception:
                    logic._api_ban_until_ms = now_ms + 120_000
            if cached:
                return cached[1]
            return {"success": False, "error": err_str,
                    "tp1_orders": [], "tp2_orders": [], "sl_orders": []}

        # ── 2. Algo open orders (trailing stops placed as algo orders) ──
        algo_orders = _fetch_algo_orders(client)

        # ── 3. Position-level TP/SL (set via Binance app on the position directly) ──
        pos_tpsl = _fetch_position_tpsl(client)

        # ── 4. DB positions for context / virtual-order fallback ──
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
        sl_orders  = []
        seen_ids   = set()

        def _add_order(o, source):
            oid = str(o.get('orderId') or o.get('algoId') or '')
            if not oid or oid in seen_ids:
                return
            seen_ids.add(oid)

            o_type  = (o.get('type') or o.get('algoType') or '').upper()
            symbol  = o.get('symbol', '')
            side    = o.get('side', '').upper()
            trigger = float(o.get('stopPrice') or o.get('triggerPrice') or o.get('price') or 0)
            qty     = float(o.get('origQty') or o.get('qty') or 0)
            bt      = o.get('time') or o.get('bookTime') or o.get('updateTime') or 0

            time_str   = datetime.fromtimestamp(int(bt) / 1000).strftime('%Y-%m-%d %H:%M:%S') if bt else 'N/A'
            db_pos     = pos_map.get(symbol)
            close_all  = o.get('closePosition', False)
            reduce_only = o.get('reduceOnly', False)

            ctx = {
                'orderId':        oid,
                'symbol':         symbol,
                'side':           side,
                'type':           o_type,
                'triggerPrice':   trigger,
                'qty':            qty,
                'time':           time_str,
                'source':         source,
                'closePosition':  close_all,
                'position_entry': float(db_pos.entry_price) if db_pos and db_pos.entry_price else None,
                'position_sl':    float(db_pos.sl_price)    if db_pos and db_pos.sl_price    else None,
                'position_tp1':   float(db_pos.tp1_price)   if db_pos and db_pos.tp1_price   else None,
            }

            # ── Classification ──
            if 'TAKE_PROFIT' in o_type:
                # Handles: TAKE_PROFIT_MARKET, TAKE_PROFIT, TAKE_PROFIT_LIMIT
                if 'LIMIT' in o_type:
                    ctx['label'] = 'TP2'
                    tp2_orders.append(ctx)
                else:
                    ctx['label'] = 'TP1'
                    tp1_orders.append(ctx)

            elif o_type == 'LIMIT' and (reduce_only or close_all):
                # Reduce-only or close-position limit orders = TP2
                ctx['label'] = 'TP2'
                tp2_orders.append(ctx)

            elif 'TRAILING' in o_type:
                ctx['label'] = 'Trail SL'
                sl_orders.append(ctx)

            elif 'STOP' in o_type:
                # Handles: STOP_MARKET, STOP, STOP_LOSS, STOP_LOSS_LIMIT
                ctx['label'] = 'SL'
                sl_orders.append(ctx)

        # Map all real Binance orders
        for o in all_orders:
            _add_order(o, 'binance')
        for o in algo_orders:
            _add_order(o, 'algo')

        # ── 5. Inject position-level TP/SL from /positionRisk v3 ──
        for p in pos_tpsl:
            sym        = p['symbol']
            amt        = p['positionAmt']
            side_close = 'SELL' if amt > 0 else 'BUY'

            if p['stopPrice'] > 0 and not any(o['symbol'] == sym for o in sl_orders):
                sl_orders.append({
                    'orderId':      f'posrisk_sl_{sym}',
                    'symbol':       sym,
                    'side':         side_close,
                    'type':         'STOP_MARKET',
                    'label':        'SL',
                    'triggerPrice': p['stopPrice'],
                    'qty':          abs(amt),
                    'time':         'Binance position',
                    'source':       'position',
                })

            if p['takeProfitPrice'] > 0 and not any(o['symbol'] == sym for o in tp1_orders):
                tp1_orders.append({
                    'orderId':      f'posrisk_tp_{sym}',
                    'symbol':       sym,
                    'side':         side_close,
                    'type':         'TAKE_PROFIT_MARKET',
                    'label':        'TP1',
                    'triggerPrice': p['takeProfitPrice'],
                    'qty':          abs(amt),
                    'time':         'Binance position',
                    'source':       'position',
                })

        # ── 6. Virtual fallback for DB positions with no matching Binance order ──
        for sym, pos in pos_map.items():
            side_close = 'SELL' if pos.side == 'LONG' else 'BUY'

            if pos.sl_price and pos.sl_price > 0 and not any(o['symbol'] == sym for o in sl_orders):
                sl_orders.append({
                    'orderId':      f'virtual_sl_{pos.id}',
                    'symbol':       sym,
                    'side':         side_close,
                    'type':         'VIRTUAL_STOP',
                    'label':        'SL',
                    'triggerPrice': float(pos.current_sl or pos.sl_price),
                    'qty':          float(pos.initial_qty),
                    'time':         pos.updated_at.strftime('%Y-%m-%d %H:%M:%S') if pos.updated_at else 'N/A',
                    'source':       'virtual',
                })

            if pos.tp1_price and pos.tp1_price > 0 and not any(o['symbol'] == sym for o in tp1_orders):
                tp1_qty = (float(pos.initial_qty) * float(pos.tp1_qty_pct) / 100.0
                           if pos.tp1_qty_pct else float(pos.initial_qty))
                tp1_orders.append({
                    'orderId':      f'virtual_tp1_{pos.id}',
                    'symbol':       sym,
                    'side':         side_close,
                    'type':         'VIRTUAL_TP',
                    'label':        'TP1',
                    'triggerPrice': float(pos.tp1_price),
                    'qty':          tp1_qty,
                    'time':         pos.updated_at.strftime('%Y-%m-%d %H:%M:%S') if pos.updated_at else 'N/A',
                    'source':       'virtual',
                })

            if pos.tp2_price and pos.tp2_price > 0 and not any(o['symbol'] == sym for o in tp2_orders):
                tp1_qty = (float(pos.initial_qty) * float(pos.tp1_qty_pct) / 100.0
                           if pos.tp1_qty_pct else 0)
                tp2_qty = float(pos.initial_qty) - tp1_qty
                if tp2_qty > 0:
                    tp2_orders.append({
                        'orderId':      f'virtual_tp2_{pos.id}',
                        'symbol':       sym,
                        'side':         side_close,
                        'type':         'VIRTUAL_LIMIT',
                        'label':        'TP2',
                        'triggerPrice': float(pos.tp2_price),
                        'qty':          tp2_qty,
                        'time':         pos.updated_at.strftime('%Y-%m-%d %H:%M:%S') if pos.updated_at else 'N/A',
                        'source':       'virtual',
                    })

        result = {
            "success":    True,
            "tp1_orders": tp1_orders,
            "tp2_orders": tp2_orders,
            "sl_orders":  sl_orders,
        }
        _tp1_sl_cache[user_id] = (now, result)
        return result

    except Exception as e:
        print(f"[ERROR] get_tp1_and_sl_orders: {e}")
        if cached:
            return cached[1]
        return {
            "success":    False,
            "error":      str(e),
            "tp1_orders": [],
            "tp2_orders": [],
            "sl_orders":  [],
        }
