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
            qty_p = price_p = 0
            for f in s["filters"]:
                if f["filterType"] == "LOT_SIZE":
                    qty_p = abs(Decimal(f["stepSize"]).as_tuple().exponent)
                if f["filterType"] == "PRICE_FILTER":
                    price_p = abs(Decimal(f["tickSize"]).as_tuple().exponent)
            return qty_p, price_p
    return 3, 2

def r_qty(v, p):
    return float(Decimal(v).quantize(Decimal(f"1e-{p}"), ROUND_DOWN))

def r_price(v, p):
    return float(Decimal(v).quantize(Decimal(f"1e-{p}"), ROUND_DOWN))

# ================= SESSION =================

def initialize_session():
    session.setdefault("trades", [])
    session.setdefault("stats", {})

# ================= BALANCE =================

def get_live_balance():
    try:
        acc = client.futures_account()
        usdt = next(i for i in acc["assets"] if i["asset"] == "USDT")
        return float(usdt["availableBalance"]) + float(usdt["initialMargin"]), float(usdt["initialMargin"])
    except:
        return None, None

# ================= SIZING =================

def calculate_position_sizing(unutilized, entry, sl_type, sl_value):
    if sl_value <= 0:
        return {"error": "SL required"}

    risk = unutilized * 0.01
    sl_pct = (sl_value / entry * 100) if sl_type == "SL Points" else sl_value
    move = sl_pct + 0.2

    notional = (risk / move) * 100
    units = notional / entry
    lev = min(100, floor(100 / move))

    return {
        "suggested_units": units,
        "suggested_leverage": max(1, lev),
        "risk_amount": round(risk, 2),
        "error": None
    }

# ================= EXECUTE =================

def execute_trade_action(balance, symbol, side, entry, order_type, sl_type,
                         sl_value, sizing, u_units, u_lev, margin_mode,
                         tp1, tp1_pct, tp2):

    today = datetime.utcnow().date().isoformat()
    stats = session["stats"].get(today, {"total": 0, "symbols": {}})

    if stats["total"] >= 4:
        return {"success": False, "message": "Daily limit reached"}

    if stats["symbols"].get(symbol, 0) >= 2:
        return {"success": False, "message": "Symbol limit reached"}

    try:
        # ---- One-way mode safe ----
        try:
            if client.futures_get_position_mode()["dualSidePosition"]:
                client.futures_change_position_mode(dualSidePosition=False)
        except:
            pass

        lev = int(u_lev if u_lev > 0 else sizing["suggested_leverage"])
        client.futures_change_leverage(symbol=symbol, leverage=lev)

        try:
            client.futures_change_margin_type(symbol=symbol, marginType=margin_mode.upper())
        except:
            pass

        qty_p, price_p = get_symbol_precisions(symbol)
        units = u_units if u_units > 0 else sizing["suggested_units"]
        qty = r_qty(abs(units), qty_p)

        main_side = Client.SIDE_BUY if side == "LONG" else Client.SIDE_SELL
        exit_side = Client.SIDE_SELL if side == "LONG" else Client.SIDE_BUY

        # ---- MAIN ORDER ----
        if order_type == "MARKET":
            client.futures_create_order(
                symbol=symbol,
                side=main_side,
                type="MARKET",
                quantity=qty
            )
        else:
            client.futures_create_order(
                symbol=symbol,
                side=main_side,
                type="LIMIT",
                price=r_price(entry, price_p),
                quantity=qty,
                timeInForce="GTC"
            )

        # ---- STOP LOSS (FULL CLOSE) ----
        if sl_value > 0:
            sl_price = (
                entry - sl_value if side == "LONG" else entry + sl_value
                if sl_type == "SL Points"
                else entry * (1 - sl_value / 100) if side == "LONG"
                else entry * (1 + sl_value / 100)
            )

            client.futures_create_order(
                symbol=symbol,
                side=exit_side,
                type="STOP_MARKET",
                stopPrice=r_price(sl_price, price_p),
                closePosition=True
            )

        # ---- TP1 (LIMIT) ----
        if tp1 > 0:
            tp1_qty = r_qty(qty * (tp1_pct / 100), qty_p)
            if tp1_qty > 0:
                client.futures_create_order(
                    symbol=symbol,
                    side=exit_side,
                    type="LIMIT",
                    price=r_price(tp1, price_p),
                    quantity=tp1_qty,
                    timeInForce="GTC",
                    reduceOnly=True
                )

        # ---- TP2 (LIMIT) ----
        if tp2 > 0:
            tp2_qty = r_qty(qty * ((100 - tp1_pct) / 100), qty_p)
            if tp2_qty > 0:
                client.futures_create_order(
                    symbol=symbol,
                    side=exit_side,
                    type="LIMIT",
                    price=r_price(tp2, price_p),
                    quantity=tp2_qty,
                    timeInForce="GTC",
                    reduceOnly=True
                )

        stats["total"] += 1
        stats["symbols"][symbol] = stats["symbols"].get(symbol, 0) + 1
        session["stats"][today] = stats
        session["trades"].append({"symbol": symbol, "side": side, "qty": qty})
        session.modified = True

        return {"success": True, "message": "Trade executed successfully"}

    except Exception as e:
        return {"success": False, "message": str(e)}

# ================= UTIL =================

def get_all_exchange_symbols():
    try:
        info = client.futures_exchange_info()
        return sorted([s["symbol"] for s in info["symbols"]
                       if s["status"] == "TRADING" and s["quoteAsset"] == "USDT"])
    except:
        return ["BTCUSDT", "ETHUSDT"]

def get_live_price(symbol):
    try:
        return float(client.futures_symbol_ticker(symbol=symbol)["price"])
    except:
        return None
