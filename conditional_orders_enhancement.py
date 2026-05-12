import logic
from models import TradePosition, db
from datetime import datetime


def invalidate_conditional_cache(user_id):
    """
    Call this immediately after placing any TP/SL order so the next poll
    fetches fresh data from Binance instead of serving the cached snapshot.
    """
    logic._conditional_cache.pop(user_id, None)


def get_tp1_and_sl_orders(user_id):
    try:
        # ── Step 1: Reuse logic.get_all_open_conditional_orders ──────────────
        # This function already handles regular + algo fetches, caching,
        # and ban-avoidance.  Doing a second independent fetch here was the
        # root cause of seeing virtual orders instead of real Binance orders
        # (the old fetch failed silently → no real orders found → virtual
        # fallbacks were injected for every DB position).
        real_orders = logic.get_all_open_conditional_orders(user_id)

        # ── Step 2: Load only OPEN DB positions for context / virtual guard ──
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

        # ── Step 3: Classify real Binance orders ─────────────────────────────
        for o in real_orders:
            # real_orders already have orderId, symbol, type, label, side,
            # stopPrice, origQty, time, source set by get_all_open_conditional_orders.
            oid = str(o.get('orderId') or '')
            if not oid or oid in seen_ids:
                continue
            seen_ids.add(oid)

            o_type  = (o.get('type') or '').upper()
            symbol  = o.get('symbol', '')
            side    = (o.get('side') or '').upper()
            trigger = float(o.get('stopPrice') or o.get('price') or 0)
            qty     = float(o.get('origQty') or 0)
            db_pos  = pos_map.get(symbol)

            context = {
                'orderId':        oid,
                'symbol':         symbol,
                'side':           side,
                'type':           o_type,
                'triggerPrice':   trigger,
                'qty':            qty,
                'time':           o.get('time', 'N/A'),
                'source':         o.get('source', 'regular'),
                'position_entry': float(db_pos.entry_price) if db_pos and db_pos.entry_price else None,
                'position_sl':    float(db_pos.sl_price)    if db_pos and db_pos.sl_price    else None,
                'position_tp1':   float(db_pos.tp1_price)   if db_pos and db_pos.tp1_price   else None,
                'position_status': db_pos.status if db_pos else 'unknown',
            }

            # Classification
            if 'TAKE_PROFIT' in o_type and 'LIMIT' not in o_type:
                context['label'] = 'TP1'
                tp1_orders.append(context)
            elif o_type == 'LIMIT' and (o.get('reduceOnly') or o.get('closePosition')):
                context['label'] = 'TP2'
                tp2_orders.append(context)
            elif 'STOP' in o_type or 'TRAILING' in o_type:
                context['label'] = 'Trail SL' if 'TRAILING' in o_type else 'SL'
                sl_orders.append(context)

        # ── Step 4: Virtual fallbacks (only when Binance has NO matching order) ──
        # These guard positions that are below Binance's 5 USDT min-notional and
        # therefore couldn't have a real order placed.  They are NEVER sent to
        # Binance; the frontend must check source == 'virtual' before showing
        # a Cancel button.
        for sym, pos in pos_map.items():
            side_close = 'SELL' if pos.side == 'LONG' else 'BUY'

            # Virtual SL
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

            # Virtual TP1
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

            # Virtual TP2
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
