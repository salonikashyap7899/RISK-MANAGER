from flask import session
from datetime import datetime
from math import floor
from decimal import Decimal, ROUND_DOWN
from binance.client import Client
import config

client = Client(config.BINANCE_KEY, config.BINANCE_SECRET)

# ================= PRECISION =================

def get_symbol_precisions(symbol):
    info = client.futures_exchange_info()
    for s in info["symbols"]:
        if s["symbol"] == symbol:
            qp = pp = 0
            for f in s["filters"]:
                if f["filterType"] == "LOT_SIZE":
                    qp = abs(Decimal(f["stepSize"]).as_tuple().exponent)
                if f["filterType"] == "PRICE_FILTER":
                    pp = abs(Decimal(f["tickSize"]).as_tuple().exponent)
            return qp, pp
    return 3, 4

def rq(v, p):
    return float(Decimal(v).quantize(Decimal(f"1e-{p}"), ROUND_DOWN))

def rp(v, p):
    return float(Decimal(v).quantize(Decimal(f"1e-{p}"), ROUND_DOWN))

# ================= SESSION =================

def initialize_session():
    session.setdefault("trades", [])
    session.setdefault("stats", {})

# ================= EXECUTION =================

def execute_trade_action(balance, symbol, side, entry, order_type,
                         sl_type, sl_value, sizing,
                         u_units, u_lev, margin_mode,
                         tp1, tp1_pct, tp2):

    today = datetime.utcnow().date().isoformat()
    stats = session["stats"].get(today, {"total": 0, "symbols": {}})

    if stats["total"] >= 4:
        return {"success": False, "message": "Daily trade limit reached"}

    try:
        # ----- Position Mode (SAFE) -----
        try:
            if client.futures_get_position_mode()["dualSidePosition"]:
                client.futures_change_position_mode(dualSidePosition=False)
        except:
            pass

        # ----- Leverage -----
        lev = int(u_lev if u_lev > 0 else sizing["suggested_leverage"])
        client.futures_change_leverage(symbol=symbol, leverage=lev)

        # ----- Precision -----
        qp, pp = get_symbol_precisions(symbol)
        units = u_units if u_units > 0 else sizing["suggested_units"]
        qty = rq(abs(units), qp)

        buy = Client.SIDE_BUY
        sell = Client.SIDE_SELL
        main = buy if side == "LONG" else sell
        exit = sell if side == "LONG" else buy

        # ===== MAIN ORDER =====
        if order_type == "MARKET":
            client.futures_create_order(
                symbol=symbol,
                side=main,
                type="MARKET",
                quantity=qty
            )
        else:
            client.futures_create_order(
                symbol=symbol,
                side=main,
                type="LIMIT",
                price=rp(entry, pp),
                quantity=qty,
                timeInForce="GTC"
            )

        # ===== STOP LOSS (LIMIT ONLY) =====
        if sl_value > 0:
            sl_price = (
                entry - sl_value if side == "LONG" else entry + sl_value
                if sl_type == "SL Points"
                else entry * (1 - sl_value / 100) if side == "LONG"
                else entry * (1 + sl_value / 100)
            )

            client.futures_create_order(
                symbol=symbol,
                side=exit,
                type="LIMIT",
                price=rp(sl_price, pp),
                quantity=qty,
                timeInForce="GTC",
                reduceOnly=True
            )

        # ===== TP1 =====
        if tp1 > 0:
            tp1_qty = rq(qty * (tp1_pct / 100), qp)
            client.futures_create_order(
                symbol=symbol,
                side=exit,
                type="LIMIT",
                price=rp(tp1, pp),
                quantity=tp1_qty,
                timeInForce="GTC",
                reduceOnly=True
            )

        # ===== TP2 =====
        if tp2 > 0:
            tp2_qty = rq(qty * ((100 - tp1_pct) / 100), qp)
            client.futures_create_order(
                symbol=symbol,
                side=exit,
                type="LIMIT",
                price=rp(tp2, pp),
                quantity=tp2_qty,
                timeInForce="GTC",
                reduceOnly=True
            )

        stats["total"] += 1
        session["stats"][today] = stats
        session["trades"].append({"symbol": symbol, "side": side, "qty": qty})
        session.modified = True

        return {"success": True, "message": "Trade placed successfully"}

    except Exception as e:
        return {"success": False, "message": str(e)}
