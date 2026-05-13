import logic
import hmac
import hashlib
import time
import requests
from models import TradePosition, db
from datetime import datetime


def invalidate_conditional_cache(user_id):
    """Clear the conditional-orders cache so next poll hits Binance fresh."""
    logic._conditional_cache.pop(user_id, None)


# ─── Direct REST fallback ──────────────────────────────────────────────────────
def _direct_binance_open_orders(user_id):
    """
    Fetch ALL open futures orders via a direct signed HTTPS request.
    Used as fallback when python-binance client's futures_get_open_orders
    fails silently (timestamp drift, library bug, etc.).
    Returns a list of raw Binance order dicts, or [] on any error.
    """
    try:
        client = logic.get_client(user_id)
        if not client:
            return []

        api_key    = getattr(client, 'API_KEY', None)
        api_secret = getattr(client, 'API_SECRET', None)
        if not api_key or not api_secret:
            return []

        # Use Binance server time to avoid -1021 timestamp errors
        ts = int(time.time() * 1000)
        try:
            srv = requests.get('https://fapi.binance.com/fapi/v1/time', timeout=5)
            ts  = srv.json().get('serverTime', ts)
        except Exception:
            pass  # use local ts if server time fetch fails

        query = f"timestamp={ts}&recvWindow=10000"
        sig   = hmac.new(
            api_secret.encode('utf-8'),
            query.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()

        url  = f"https://fapi.binance.com/fapi/v1/openOrders?{query}&signature={sig}"
        resp = requests.get(url, headers={"X-MBX-APIKEY": api_key}, timeout=10)

        if resp.status_code == 200:
            orders = resp.json()
            print(f"[DIRECT REST] Got {len(orders)} open orders from Binance for user {user_id}")
            return orders if isinstance(orders, list) else []
        else:
            print(f"[DIRECT REST] Binance returned {resp.status_code}: {resp.text[:200]}")
            return []

    except Exception as e:
        print(f"[DIRECT REST] Exception fetching open orders: {e}")
        return []


# ─── Classify a list of raw Binance order dicts ────────────────────────────────
def _classify_raw_orders(raw_orders, pos_map, seen_ids, tp1_orders, tp2_orders, sl_orders):
    """Sort raw Binance order dicts into tp1 / tp2 / sl buckets."""
    for o in raw_orders:
        oid = str(o.get('orderId') or o.get('algoId') or '')
        if not oid or oid in seen_ids:
            continue
        seen_ids.add(oid)

        o_type  = (o.get('type') or o.get('algoType') or '').upper()
        symbol  = o.get('symbol', '')
        side    = (o.get('side') or '').upper()
        trigger = float(o.get('stopPrice') or o.get('triggerPrice') or o.get('price') or 0)
        qty     = float(o.get('origQty') or o.get('qty') or 0)
        ts_raw  = o.get('time') or o.get('bookTime') or o.get('updateTime') or 0
        time_str = (datetime.fromtimestamp(int(ts_raw) / 1000).strftime('%Y-%m-%d %H:%M:%S')
                    if ts_raw else 'N/A')

        db_pos = pos_map.get(symbol)
        context = {
            'orderId':         oid,
            'symbol':          symbol,
            'side':            side,
            'type':            o_type,
            'triggerPrice':    trigger,
            'qty':             qty,
            'time':            time_str,
            'source':          o.get('source', 'regular'),
            'position_entry':  float(db_pos.entry_price) if db_pos and db_pos.entry_price else None,
            'position_sl':     float(db_pos.sl_price)    if db_pos and db_pos.sl_price    else None,
            'position_tp1':    float(db_pos.tp1_price)   if db_pos and db_pos.tp1_price   else None,
            'position_status': db_pos.status if db_pos else 'unknown',
        }

        if 'TAKE_PROFIT' in o_type and 'LIMIT' not in o_type:
            context['label'] = 'TP1'
            tp1_orders.append(context)
        elif o_type == 'LIMIT' and (o.get('reduceOnly') or o.get('closePosition')):
            context['label'] = 'TP2'
            tp2_orders.append(context)
        elif 'STOP' in o_type or 'TRAILING' in o_type:
            context['label'] = 'Trail SL' if 'TRAILING' in o_type else 'SL'
            sl_orders.append(context)


# ─── Main public function ──────────────────────────────────────────────────────
def get_tp1_and_sl_orders(user_id):
    try:
        # ── 1. Load open DB positions for context & virtual-guard fallbacks ──
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

        tp1_orders, tp2_orders, sl_orders, seen_ids = [], [], [], set()

        # ── 2. Primary: reuse logic layer (cached, handles regular + algo) ───
        real_orders = logic.get_all_open_conditional_orders(user_id)
        if real_orders:
            _classify_raw_orders(real_orders, pos_map, seen_ids,
                                 tp1_orders, tp2_orders, sl_orders)

        # ── 3. Fallback: direct signed REST call when client returned nothing ─
        # If the logic layer returned 0 orders BUT we have open DB positions,
        # the client fetch almost certainly failed silently.  Hit Binance
        # directly so we never show fake virtual orders for real Binance orders.
        real_count = len(tp1_orders) + len(tp2_orders) + len(sl_orders)
        if real_count == 0 and pos_map:
            print(f"[FALLBACK] logic layer returned 0 orders but {len(pos_map)} "
                  "DB positions exist — trying direct REST fetch")
            raw = _direct_binance_open_orders(user_id)
            if raw:
                # Refresh the shared cache so other pollers benefit too
                logic._conditional_cache[user_id] = (int(time.time() * 1000), raw)
                _classify_raw_orders(raw, pos_map, seen_ids,
                                     tp1_orders, tp2_orders, sl_orders)

        # ── 4. Virtual fallbacks (only for genuinely missing/too-small orders) ──
        for sym, pos in pos_map.items():
            side_close = 'SELL' if pos.side == 'LONG' else 'BUY'

            if (pos.sl_price and pos.sl_price > 0
                    and not any(o['symbol'] == sym for o in sl_orders)):
                sl_orders.append({
                    'orderId':      f"virtual_sl_{pos.id}",
                    'symbol':       sym,
                    'side':         side_close,
                    'type':         'VIRTUAL_STOP',
                    'label':        'SL',
                    'triggerPrice': float(pos.current_sl or pos.sl_price),
                    'qty':          float(pos.initial_qty),
                    'time':         pos.updated_at.strftime('%Y-%m-%d %H:%M:%S') if pos.updated_at else 'N/A',
                    'source':       'virtual',
                })

            if (pos.tp1_price and pos.tp1_price > 0
                    and not any(o['symbol'] == sym for o in tp1_orders)):
                tp1_qty = (float(pos.initial_qty) * (float(pos.tp1_qty_pct) / 100.0)
                           if pos.tp1_qty_pct else float(pos.initial_qty))
                tp1_orders.append({
                    'orderId':      f"virtual_tp1_{pos.id}",
                    'symbol':       sym,
                    'side':         side_close,
                    'type':         'VIRTUAL_TP',
                    'label':        'TP1',
                    'triggerPrice': float(pos.tp1_price),
                    'qty':          tp1_qty,
                    'time':         pos.updated_at.strftime('%Y-%m-%d %H:%M:%S') if pos.updated_at else 'N/A',
                    'source':       'virtual',
                })

            if (pos.tp2_price and pos.tp2_price > 0
                    and not any(o['symbol'] == sym for o in tp2_orders)):
                tp1_qty = (float(pos.initial_qty) * (float(pos.tp1_qty_pct) / 100.0)
                           if pos.tp1_qty_pct else 0)
                tp2_qty = float(pos.initial_qty) - tp1_qty
                if tp2_qty > 0:
                    tp2_orders.append({
                        'orderId':      f"virtual_tp2_{pos.id}",
                        'symbol':       sym,
                        'side':         side_close,
                        'type':         'VIRTUAL_LIMIT',
                        'label':        'TP2',
                        'triggerPrice': float(pos.tp2_price),
                        'qty':          tp2_qty,
                        'time':         pos.updated_at.strftime('%Y-%m-%d %H:%M:%S') if pos.updated_at else 'N/A',
                        'source':       'virtual',
                    })

        return {
            "success":    True,
            "tp1_orders": tp1_orders,
            "tp2_orders": tp2_orders,
            "sl_orders":  sl_orders,
        }

    except Exception as e:
        print(f"[ERROR] get_tp1_and_sl_orders: {e}")
        return {
            "success":    False,
            "error":      str(e),
            "tp1_orders": [],
            "tp2_orders": [],
            "sl_orders":  [],
        }
