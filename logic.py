from flask import session
from datetime import datetime
from math import floor
from decimal import Decimal, ROUND_DOWN
from binance.client import Client
import config

client = Client(config.BINANCE_KEY, config.BINANCE_SECRET)

# =========================
# PRECISION HELPERS
# =========================

def get_symbol_precisions(symbol):
    info = client.futures_exchange_info()
    for s in info["symbols"]:
        if s["symbol"] == symbol:
            qty_precision = 0
            price_precision = 0
            for f in s["filters"]:
                if f["filterType"] == "LOT_SIZE":
                    qty_precision = abs(Decimal(f["stepSize"]).as_tuple().exponent)
                if f["filterType"] == "PRICE_FILTER":
                    price_precision = abs(Decimal(f["tickSize"]).as_tuple().exponent)
            return qty_precision, price_precision
    return 3, 2  # fallback

def round_qty(val, precision):
    return float(Decimal(val).quantize(
        Decimal(f"1e-{precision}"), rounding=ROUND_DOWN))

def round_price(val, precision):
    return float(Decimal(val).quantize(
        Decimal(f"1e-{precision}"), rounding=ROUND_DOWN))

# =========================
# SESSION
# =========================

def initialize_session():
    session.setdefault("trades", [])
    session.setdefault("stats", {})

# =========================
# BALANCE
# =========================

def get_live_balance():
    try:
        acc = client.futures_account()
        usdt = next(i for i in acc["assets"] if i["asset"] == "USDT")
        available = float(usdt["availableBalance"])
        margin = float(usdt["initialMargin"])
        return available + margin, margin
    except:
        return None, None

# =========================
# POSITION SIZING
# =========================

def calculate_position_sizing(unutilized_margin, entry, sl_type, sl_value):
    if sl_value <= 0:
        return {"error": "SL required"}

    risk = unutilized_margin * 0.01
    sl_pct = (sl_value / entry * 100) if sl_type == "SL Points" else sl_value
    movement = sl_pct + 0.2

    notional = (risk / movement) * 100
    units = notional / entry
    leverage = min(100, floor(100 / movement))

    return {
        "suggested_units": units,
        "suggested_leverage": max(1, leverage),
        "risk_amount": round(risk, 2),
        "error": None
    }

# =========================
# EXECUTE TRADE
# =========================

def execute_trade_action(balance, symbol, side, entry, order_type, sl_type,
                         sl_value, sizing, u_units, u_lev, margin_mode,
                         tp1, tp1_pct, tp2):

    today = datetime.utcnow().date().isoformat()
    stats = session["stats"].get(today, {"total": 0, "symbols": {}})

    if stats["total"] >= 4:
        return {"success": False, "message": "Daily trade limit reached"}

    if stats["symbols"].get(symbol, 0) >= 2:
        return {"success": False, "message": "Symbol trade limit reached"}

    try:
        # -------- POSITION MODE SAFE CHECK (FIX -4059) --------
        try:
            mode = client.futures_get_position_mode()
            if mode.get("dualSidePosition"):
                client.futures_change_position_mode(dualSidePosition=False)
        except:
            pass

        # -------- LEVERAGE & MARGIN --------
        lev = int(u_lev if u_lev > 0 else sizing["suggested_leverage"])
        client.futures_change_leverage(symbol=symbol, leverage=lev)

        try:
            client.futures_change_margin_type(
                symbol=symbol, marginType=margin_mode.upper())
        except:
            pass

        # -------- PRECISION --------
        qty_p, price_p = get_symbol_precisions(symbol)

        units = u_units if u_units > 0 else sizing["suggested_units"]
        qty = round_qty(abs(units), qty_p)

        side_main = Client.SIDE_BUY if side == "LONG" else Client.SIDE_SELL

        # -------- MAIN ORDER --------
        if order_type == "MARKET":
            client.futures_create_order(
                symbol=symbol,
                side=side_main,
                type="MARKET",
                quantity=qty
            )
        else:
            client.futures_create_order(
                symbol=symbol,
                side=side_main,
                type="LIMIT",
                quantity=qty,
                price=round_price(entry, price_p),
                timeInForce="GTC"
            )

        # -------- STOP LOSS --------
        if sl_value > 0:
            if sl_type == "SL Points":
                sl_price = entry - sl_value if side == "LONG" else entry + sl_value
            else:
                sl_price = entry * (1 - sl_value / 100) if side == "LONG" else entry * (1 + sl_value / 100)

            client.futures_create_order(
                symbol=symbol,
                side=Client.SIDE_SELL if side == "LONG" else Client.SIDE_BUY,
                type="STOP_MARKET",
                stopPrice=round_price(sl_price, price_p),
                closePosition=True
            )

        # -------- TAKE PROFITS --------
        if tp1 > 0:
            tp1_qty = round_qty(qty * (tp1_pct / 100), qty_p)
            if tp1_qty > 0:
                client.futures_create_order(
                    symbol=symbol,
                    side=Client.SIDE_SELL if side == "LONG" else Client.SIDE_BUY,
                    type="TAKE_PROFIT_MARKET",
                    stopPrice=round_price(tp1, price_p),
                    quantity=tp1_qty
                )

        if tp2 > 0:
            tp2_qty = round_qty(qty * ((100 - tp1_pct) / 100), qty_p)
            if tp2_qty > 0:
                client.futures_create_order(
                    symbol=symbol,
                    side=Client.SIDE_SELL if side == "LONG" else Client.SIDE_BUY,
                    type="TAKE_PROFIT_MARKET",
                    stopPrice=round_price(tp2, price_p),
                    quantity=tp2_qty
                )

        # -------- STATS --------
        stats["total"] += 1
        stats["symbols"][symbol] = stats["symbols"].get(symbol, 0) + 1
        session["stats"][today] = stats
        session["trades"].append({
            "time": datetime.utcnow().isoformat(),
            "symbol": symbol,
            "side": side,
            "qty": qty
        })
        session.modified = True

        return {"success": True, "message": "Trade placed successfully"}

    except Exception as e:
        return {"success": False, "message": str(e)}

# =========================
# UTIL
# =========================

def get_all_exchange_symbols():
    try:
        info = client.futures_exchange_info()
        return sorted([
            s["symbol"] for s in info["symbols"]
            if s["status"] == "TRADING" and s["quoteAsset"] == "USDT"
        ])
    except:
        return ["BTCUSDT", "ETHUSDT"]

def get_live_price(symbol):
    try:
        return float(client.futures_symbol_ticker(symbol=symbol)["price"])
    except:
        return None
