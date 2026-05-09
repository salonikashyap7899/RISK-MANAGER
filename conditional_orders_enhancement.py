import logic

from datetime import datetime


def _safe_float(v, default=0.0):
    try:
        if v is None:
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _build_context(o, db_pos):
    """Build a single order context dict matching what the frontend expects."""
    order_type = (o.get('type') or '').upper()
    label      = (o.get('label') or '').upper()

    return {
        'orderId'        : o.get('orderId'),
        'symbol'         : o.get('symbol', ''),
        'side'           : o.get('side', ''),
        'type'           : order_type,
        'label'          : label,
        'triggerPrice'   : _safe_float(o.get('stopPrice', 0)),
        'price'          : _safe_float(o.get('price', 0)),
        'qty'            : _safe_float(o.get('origQty', 0)),
        'time'           : o.get('time', 'N/A'),
        'source'         : o.get('source', 'regular'),
        'reduceOnly'     : bool(o.get('reduceOnly', False)),

        # Position context from local DB (None if no record yet)
        'position_entry' : _safe_float(getattr(db_pos, 'entry_price', None), None) if db_pos else None,
        'position_sl'    : _safe_float(getattr(db_pos, 'sl_price',    None), None) if db_pos else None,
        'position_tp1'   : _safe_float(getattr(db_pos, 'tp1_price',   None), None) if db_pos else None,
        'position_tp2'   : _safe_float(getattr(db_pos, 'tp2_price',   None), None) if db_pos else None,
        'position_status': getattr(db_pos, 'status', 'unknown') if db_pos else 'unknown',
    }


def get_tp1_and_sl_orders(user_id):
    """
    Returns:
        {
          "success":     True | False,
          "tp1_orders":  [...],
          "tp2_orders":  [...],
          "sl_orders":   [...],
          "error":       "..."   (only when success is False)
        }

    Classification rules (matches Binance UI):
      TP1 = TAKE_PROFIT, TAKE_PROFIT_MARKET, TAKE_PROFIT_LIMIT  -> Conditional tab
      SL  = STOP, STOP_MARKET, STOP_LOSS, STOP_LOSS_LIMIT       -> Conditional tab
            (TRAILING_STOP_MARKET is also bucketed here)
      TP2 = a reduce-only LIMIT order                          -> Basic tab
    """
    empty = {"tp1_orders": [], "tp2_orders": [], "sl_orders": []}

    # 1) Fetch raw conditional orders from Binance via existing logic helper.
    try:
        import logic
        all_conditional = logic.get_all_open_conditional_orders(user_id) or []
    except Exception as e:
        print(f"[ERROR] get_tp1_and_sl_orders: failed fetching open orders: {e}")
        return {"success": False, "error": f"open_orders_fetch_failed: {e}", **empty}

    # 2) Build a {symbol: TradePosition} map from the user's recent DB positions.
    pos_map = {}
    try:
        from models import TradePosition
        db_positions = (
            TradePosition.query
            .filter_by(user_id=user_id)
            .order_by(TradePosition.created_at.desc())
            .limit(50)
            .all()
        )
        # Prefer OPEN positions when the same symbol appears twice
        for p in db_positions:
            existing = pos_map.get(p.symbol)
            if existing is None or (p.status == 'open' and existing.status != 'open'):
                pos_map[p.symbol] = p
    except Exception as e:
        # DB lookup is non-fatal — we can still return the orders without context.
        print(f"[WARN] get_tp1_and_sl_orders: DB position lookup failed: {e}")

    # 3) Classify every order.
    tp1_orders, tp2_orders, sl_orders = [], [], []

    for o in all_conditional:
        try:
            order_type = (o.get('type') or '').upper()
            label      = (o.get('label') or '').upper()
            symbol     = o.get('symbol', '') or ''
            reduce_only = str(o.get('reduceOnly', '')).lower() == 'true'

            is_tp1 = (label == 'TP1') or ('TAKE_PROFIT' in order_type)
            is_tp2 = (label == 'TP2') or (order_type == 'LIMIT' and reduce_only)
            is_sl  = (label == 'SL')  or (
                ('STOP' in order_type or 'STOP_LOSS' in order_type)
                and ('TRAILING' not in order_type)
                and not is_tp1
            )

            if not (is_tp1 or is_tp2 or is_sl):
                continue  # ignore everything else

            ctx = _build_context(o, pos_map.get(symbol))

            if is_tp1:
                tp1_orders.append(ctx)
            elif is_tp2:
                tp2_orders.append(ctx)
            else:
                sl_orders.append(ctx)
        except Exception as inner:
            # Never let one bad order break the whole response.
            print(f"[WARN] get_tp1_and_sl_orders: skipping malformed order {o}: {inner}")
            continue

    return {
        "success"   : True,
        "tp1_orders": tp1_orders,
        "tp2_orders": tp2_orders,
        "sl_orders" : sl_orders,
        "total"     : len(tp1_orders) + len(tp2_orders) + len(sl_orders),
        "fetched_at": datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'),
    }
