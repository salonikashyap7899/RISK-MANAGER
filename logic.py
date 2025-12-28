from flask import session
from datetime import datetime
from math import floor
from decimal import Decimal
from time import sleep
from binance.client import Client
import config

client = Client(config.BINANCE_KEY, config.BINANCE_SECRET)

# ======================================================
# PRECISION (TICK SIZE SAFE)
# ======================================================

def get_symbol_steps(symbol):
    info = client.futures_exchange_info()
    for s in info["symbols"]:
        if s["symbol"] == symbol:
            qty_step = price_step = None
            for f in s["filters"]:
                if f["filterType"] == "LOT_SIZE":
                    qty_step = Decimal(f["stepSize"])
                if f["filterType"] == "PRICE_FILTER":
                    price_step = Decimal(f["tickSize"])
            return qty_step, price_step
    return Decimal("0.001"), Decimal("0.0001")

def round_qty(value, step):
    return float((Decimal(value) // step) * step)

def round_price(value, step):
    return float((Decimal(value) // step) * step)

# ======================================================
# SESSION
# ======================================================

def initialize_session():
    session.setdefault("trades", [])
    session.setdefault("stats", {})

# ======================================================
# BALANCE
# ======================================================

def get_live_balance():
    try:
        acc = client.futures_account()
        usdt = next(i for i in acc["assets"] if i["asset"] == "USDT")
        return float(usdt["availableBalance"]) + float(usdt["initialMargin"]), float(usdt["initialMargin"])
    except:
        return None, None

# ======================================================
# POSITION SIZING (UNCHANGED)
# ======================================================

def calculate_position_sizing(unutilized_margin, entry, sl_type, sl_value):
    try:
        if sl_value <= 0:
            return {"error": "SL Required"}

        risk_amount = unutilized_margin * 0.01
        sl_pct = (sl_value / entry * 100) if sl_type == "SL Points" else sl_value
        movement = sl_pct + 0.2

        notional = (risk_amount / movement) * 100
        units = notional / entry
        leverage = min(100, floor(100 / movement))

        return {
            "suggested_units": units,
            "suggested_leverage": int(max(1, leverage)),
            "risk_amount": round(risk_amount, 2),
            "error": None
        }
    except:
        return {"error": "Invalid sizing"}

# ======================================================
# WAIT FOR POSITION (FIX -2022)
# ======================================================

def wait_for_position(symbol, side, timeout=5):
    for _ in range(timeout * 5):
        positions = client.futures_position_information(symbol=symbol)
        for p in positions:
            amt = float(p["positionAmt"])
            if side == "LONG" and amt > 0:
                return abs(amt)
            if side == "SHORT" and amt < 0:
                return abs(amt)
        sleep(0.2)
    return 0

# ======================================================
# EXECUTE TRADE (ALL ERRORS FIXED)
# ======================================================

def execute_trade_action(balance, symbol, side, entry, order_type,
                         sl_type, sl_value, sizing,
                         u_units, u_lev, margin_mode,
                         tp1, tp1_pct, tp2):

    today = datetime.utcnow().date().isoformat()
    stats = session["stats"].get(today, {"total": 0, "symbols": {}})

    if stats["total"] >= 4:
        return {"success": False, "message": "Daily trade limit reached"}

    try:
        # ---------- POSITION MODE ----------
        try:
            if client.futures_get_position_mode()["dualSidePosition"]:
                client.futures_change_position_mode(dualSidePosition=False)
        except:
            pass

        # ---------- LEVERAGE ----------
        lev = int(u_lev if u_lev > 0 else sizing["suggested_leverage"])
        client.futures_change_leverage(symbol=symbol, leverage=lev)

        # ---------- PRECISION ----------
        qty_step, price_step = get_symbol_steps(symbol)
        units = u_units if u_units > 0 else sizing["suggested_units"]
        qty = round_qty(abs(units), qty_step)

        buy = Client.SIDE_BUY
        sell = Client.SIDE_SELL
        main_side = buy if side == "LONG" else sell
        exit_side = sell if side == "LONG" else buy

        # ================= MAIN ORDER =================
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
                price=round_price(entry, price_step),
                quantity=qty,
                timeInForce="GTC"
            )

        # ---------- WAIT FOR POSITION (CRITICAL) ----------
        filled_qty = wait_for_position(symbol, side)
        if filled_qty <= 0:
            return {"success": False, "message": "Position not confirmed, TP/SL skipped"}

        # ================= STOP LOSS =================
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
                type="LIMIT",
                price=round_price(sl_price, price_step),
                quantity=filled_qty,
                timeInForce="GTC",
                reduceOnly=True
            )

        # ================= TP1 =================
        if tp1 > 0:
            tp1_qty = round_qty(filled_qty * (tp1_pct / 100), qty_step)
            if tp1_qty > 0:
                client.futures_create_order(
                    symbol=symbol,
                    side=exit_side,
                    type="LIMIT",
                    price=round_price(tp1, price_step),
                    quantity=tp1_qty,
                    timeInForce="GTC",
                    reduceOnly=True
                )

        # ================= TP2 =================
        if tp2 > 0:
            tp2_qty = round_qty(filled_qty * ((100 - tp1_pct) / 100), qty_step)
            if tp2_qty > 0:
                client.futures_create_order(
                    symbol=symbol,
                    side=exit_side,
                    type="LIMIT",
                    price=round_price(tp2, price_step),
                    quantity=tp2_qty,
                    timeInForce="GTC",
                    reduceOnly=True
                )

        # ---------- LOG ----------
        stats["total"] += 1
        session["stats"][today] = stats
        session["trades"].append({
            "symbol": symbol,
            "side": side,
            "qty": filled_qty,
            "time": datetime.utcnow().isoformat()
        })
        session.modified = True

        return {"success": True, "message": "Trade executed successfully"}

    except Exception as e:
        return {"success": False, "message": str(e)}

# ======================================================
# UTIL
# ======================================================

def get_all_exchange_symbols():
    try:
        info = client.futures_exchange_info()
        return sorted(s["symbol"] for s in info["symbols"]
                      if s["status"] == "TRADING" and s["quoteAsset"] == "USDT")
    except:
        return ["BTCUSDT", "ETHUSDT"]

def get_live_price(symbol):
    try:
        return float(client.futures_symbol_ticker(symbol=symbol)["price"])
    except:
        return None
