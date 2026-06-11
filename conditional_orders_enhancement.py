import logic
from models import TradePosition, db
from datetime import datetime
import time

# Per-user result cache for get_tp1_and_sl_orders
# Prevents repeated Binance calls on every frontend poll
_tp1_sl_cache = {}           # {user_id: (ts_seconds, result_dict)}
TP1_SL_CACHE_DURATION = 10  # seconds — short enough for near-real-time updates


def invalidate_cache(user_id):
    """Call this after placing or cancelling orders so the next poll fetches fresh data."""
    _tp1_sl_cache.pop(user_id, None)
    # Also invalidate the main conditional orders cache in logic.py
    logic._conditional_cache.pop(user_id, None)



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
            print(f"[DEBUG] ✅ Regular open orders fetched: {len(all_orders)} orders")
        except Exception as e:
            err_str = str(e)
            print(f"❌ Error fetching open orders: {e}")
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

        # Fetch conditional/algo orders using the library's built-in conditional=True flag.
        # This correctly calls /fapi/v1/openAlgoOrders and avoids the KeyError: 'data'
        # bug that occurs when passing loose kwargs to signed _request_futures_api calls.
        algo_orders = []
        try:
            algo_resp = client.futures_get_open_orders(conditional=True, recvWindow=10000)
            algo_orders = algo_resp if isinstance(algo_resp, list) else (algo_resp.get('orders') or [])
            print(f"[algo_orders] ✅ Fetched {len(algo_orders)} conditional/algo orders")
        except Exception as e:
            algo_err_str = str(e)
            print(f"[algo_orders] ❌ conditional fetch failed: {algo_err_str}")
            # -4120 = algo orders not supported for this account type — non-fatal, skip silently
            if '-4120' not in algo_err_str:
                print(f"[algo_orders] ℹ️ If you see this repeatedly, check API permissions")

        # Get ONLY OPEN DB positions to provide context and virtual guard fallbacks
        # CRITICAL: Only consider positions that have a TP1 or TP2 price set in the DB
        # This prevents 'ghost' virtual orders for positions that were closed or never had TP/SL
        db_positions = (
            TradePosition.query
            .filter_by(user_id=user_id, status='open')
            .filter( (TradePosition.tp1_price > 0) | (TradePosition.tp2_price > 0) | (TradePosition.sl_price > 0) )
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
                'is_live': True, # Explicitly mark as live Binance data
                'position_entry': float(db_pos.entry_price) if db_pos and db_pos.entry_price else None,
                'position_sl': float(db_pos.sl_price) if db_pos and db_pos.sl_price else None,
                'position_tp1': float(db_pos.tp1_price) if db_pos and db_pos.tp1_price else None,
                'position_status': db_pos.status if db_pos else 'unknown',
            }

            # Classification Logic
            # 1. TP1: Take profit market (no limit)
            if 'TAKE_PROFIT' in o_type and 'LIMIT' not in o_type:
                context['label'] = 'TP1'
                tp1_orders.append(context)
                print(f"[_add_order] Added TP1 order: {oid} ({o_type}) from {source}")
            # 2. TP2: Take profit limit OR reduce-only limit order
            elif 'TAKE_PROFIT' in o_type and 'LIMIT' in o_type:
                context['label'] = 'TP2'
                tp2_orders.append(context)
                print(f"[_add_order] Added TP2 (TP_LIMIT) order: {oid} ({o_type}) from {source}")
            elif o_type == 'LIMIT' or o_type == 'LIMIT_MAKER':
                if o.get('reduceOnly') or o.get('closePosition'):
                    context['label'] = 'TP2'
                    tp2_orders.append(context)
                    print(f"[_add_order] Added TP2 (reduce-only LIMIT) order: {oid} from {source}")
                else:
                    print(f"[_add_order] Skipping non-reduceOnly LIMIT order: {oid}")
            # 3. SL: Stop market, Stop, Stop Loss, or Trailing stop
            elif 'STOP' in o_type or 'TRAILING' in o_type:
                context['label'] = 'Trail SL' if 'TRAILING' in o_type else 'SL'
                sl_orders.append(context)
                print(f"[_add_order] Added SL order: {oid} ({o_type} → {context['label']}) from {source}")
            else:
                print(f"[_add_order] Unclassified order type: {o_type} for {oid} — skipped")

        # Map all real Binance orders
        print(f"[get_tp1_and_sl_orders] Processing {len(all_orders)} regular orders + {len(algo_orders)} algo orders")
        for o in all_orders:
            _add_order(o, 'regular')
        for o in algo_orders:
            _add_order(o, 'algo')

        print(f"[get_tp1_and_sl_orders] After mapping: {len(tp1_orders)} TP1, {len(tp2_orders)} TP2, {len(sl_orders)} SL")

        # Enrich with mark price for distance-to-trigger display
        all_found_orders = tp1_orders + tp2_orders + sl_orders
        unique_symbols = list(set(o['symbol'] for o in all_found_orders if o.get('symbol')))
        mark_prices = {}
        for sym in unique_symbols:
            try:
                # Use a fast mark price fetch
                mp_data = client.futures_mark_price(symbol=sym)
                mark_prices[sym] = float(mp_data.get('markPrice', 0))
            except Exception:
                mark_prices[sym] = 0

        for o in all_found_orders:
            o['mark_price'] = mark_prices.get(o['symbol'], 0)

        # INJECT VIRTUAL ORDERS for positions that failed to place on Binance (e.g. < 5 USDT Min Notional)
        print(f"[get_tp1_and_sl_orders] Checking {len(pos_map)} DB positions for virtual orders...")
        for sym, pos in pos_map.items():
            side_close = 'SELL' if pos.side == 'LONG' else 'BUY'

            # Virtual SL
            # Only inject virtual SL if there's no live SL AND the position is still open in DB
            if pos.sl_price and pos.sl_price > 0 and not any(o['symbol'] == sym and o['label'] == 'SL' for o in sl_orders):
                print(f"[get_tp1_and_sl_orders] 📌 Injecting virtual SL for {sym}")
                sl_orders.append({
                    'orderId': f"virtual_sl_{pos.id}",
                    'symbol': sym,
                    'side': side_close,
                    'type': 'VIRTUAL_STOP',
                    'label': 'SL',
                    'triggerPrice': float(pos.current_sl or pos.sl_price),
                    'qty': float(pos.initial_qty),
                    'time': pos.updated_at.strftime('%Y-%m-%d %H:%M:%S') if pos.updated_at else 'N/A',
                    'source': 'virtual',
                    'is_live': False
                })

            # Virtual TP1
            # Only inject virtual TP1 if there's no live TP1 AND the position is still open in DB
            if pos.tp1_price and pos.tp1_price > 0 and not any(o['symbol'] == sym and o['label'] == 'TP1' for o in tp1_orders):
                print(f"[get_tp1_and_sl_orders] 📌 Injecting virtual TP1 for {sym}")
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
                    'source': 'virtual',
                    'is_live': False
                })

            # Virtual TP2
            # Only inject virtual TP2 if there's no live TP2 AND the position is still open in DB
            if pos.tp2_price and pos.tp2_price > 0 and not any(o['symbol'] == sym and o['label'] == 'TP2' for o in tp2_orders):
                print(f"[get_tp1_and_sl_orders] 📌 Injecting virtual TP2 for {sym}")
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
                        'source': 'virtual',
                        'is_live': False
                    })

        result = {
            "success": True,
            "tp1_orders": tp1_orders,
            "tp2_orders": tp2_orders,
            "sl_orders": sl_orders,
        }

        print(f"[get_tp1_and_sl_orders] Final result: {len(tp1_orders)} TP1, {len(tp2_orders)} TP2, {len(sl_orders)} SL")

        # Store in cache so repeated polls don't hammer Binance
        _tp1_sl_cache[user_id] = (now, result)
        return result

    except Exception as e:
        print(f"❌ [ERROR] get_tp1_and_sl_orders: {e}")
        import traceback
        traceback.print_exc()
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
