from flask import session
from datetime import datetime
from binance.client import Client
import math
import config

client = Client(config.BINANCE_KEY, config.BINANCE_SECRET)

# ---------------- SESSION ----------------
def initialize_session():
    if "trades" not in session:
        session["trades"] = []
    if "stats" not in session:
        session["stats"] = {}

# ---------------- BINANCE HELPERS ----------------
def get_all_exchange_symbols():
    try:
        info = client.futures_exchange_info()
        return sorted([
            s["symbol"] for s in info["symbols"]
            if s["status"] == "TRADING" and s["quoteAsset"] == "USDT"
        ])
    except:
        return ["BTCUSDT", "ETHUSDT"]

def get_live_balance():
    try:
        acc = client.futures_account()
        return float(acc["totalWalletBalance"]), float(acc["totalInitialMargin"])
    except:
        return None, None

def get_live_price(symbol):
    try:
        return float(client.futures_symbol_ticker(symbol=symbol)["price"])
    except:
        return None

# ---------------- PRECISION HANDLER ----------------
def adjust_quantity(symbol, qty):
    try:
        info = client.futures_exchange_info()
        for s in info["symbols"]:
            if s["symbol"] == symbol:
                for f in s["filters"]:
                    if f["filterType"] == "LOT_SIZE":
                        step = float(f["stepSize"])
                        precision = int(round(-math.log(step, 10), 0))
                        return round(qty - (qty % step), precision)
        return qty
    except:
        return qty

# ---------------- POSITION SIZE + LEVERAGE ----------------
def calculate_position_sizing(unutilized_margin, entry, sl_type, sl_value):
    try:
        if sl_value <= 0 or entry <= 0:
            return {"error": "Invalid SL"}

        # ---- FIXED 1% RISK ----
        risk_amount = unutilized_margin * 0.01

        # ---- SL % MOVEMENT ONLY ----
        effective_sl = sl_value + 0.2

        # ---- POSITION SIZE (USDT NOTIONAL) ----
        notional = (risk_amount / effective_sl) * 100

        # ---- CONVERT TO QUANTITY ----
        quantity = notional / entry

        # ---- LEVERAGE ----
        leverage = int(100 / effective_sl)
        leverage = max(1, min(leverage, 100))

        return {
            "suggested_units": quantity,
            "suggested_leverage": leverage,
            "risk_amount": round(risk_amount, 2),
            "error": None
        }

    except Exception as e:
        return {"error": str(e)}

# ---------------- EXECUTE TRADE ----------------
def execute_trade_action(
    balance, symbol, side, entry, order_type,
    sl_type, sl_value, sizing,
    user_units, user_lev, margin_mode,
    tp1, tp1_pct, tp2
):
    today = datetime.utcnow().date().isoformat()
    day_stats = session["stats"].get(today, {"total": 0, "symbols": {}})

    # ---- LIMITS ----
    if day_stats["total"] >= 4:
        return {"success": False, "message": "Daily trade limit (4) reached"}

    if day_stats["symbols"].get(symbol, 0) >= 2:
        return {"success": False, "message": f"{symbol} daily limit (2) reached"}

    try:
        # ---- QUANTITY ----
        raw_units = user_units if user_units > 0 else sizing["suggested_units"]
        units = adjust_quantity(symbol, raw_units)

        if units <= 0:
            return {"success": False, "message": "Order size too small for this symbol"}

        # ---- LEVERAGE (IGNORE DEFAULT 100) ----
        if user_lev and user_lev != 100:
            leverage = int(user_lev)
        else:
            leverage = int(sizing["suggested_leverage"])

        leverage = max(1, min(leverage, 100))

        # ---- BINANCE SETTINGS ----
        try:
            client.futures_change_margin_type(symbol=symbol, marginType=margin_mode.upper())
        except:
            pass

        client.futures_change_leverage(symbol=symbol, leverage=leverage)

        side_binance = Client.SIDE_BUY if side == "LONG" else Client.SIDE_SELL

        # ---- MAIN ORDER ----
        if order_type == "MARKET":
            client.futures_create_order(
                symbol=symbol,
                side=side_binance,
                type="MARKET",
                quantity=units
            )
        else:
            client.futures_create_order(
                symbol=symbol,
                side=side_binance,
                type="LIMIT",
                price=str(entry),
                timeInForce="GTC",
                quantity=units
            )

        # ---- TAKE PROFITS ----
        tp_side = Client.SIDE_SELL if side == "LONG" else Client.SIDE_BUY

        if tp1 > 0:
            q1 = adjust_quantity(symbol, units * (tp1_pct / 100))
            if q1 > 0:
                client.futures_create_order(
                    symbol=symbol,
                    side=tp_side,
                    type="LIMIT",
                    price=str(tp1),
                    timeInForce="GTC",
                    quantity=q1
                )

        if tp2 > 0:
            q2 = adjust_quantity(symbol, units - (units * (tp1_pct / 100)))
            if q2 > 0:
                client.futures_create_order(
                    symbol=symbol,
                    side=tp_side,
                    type="LIMIT",
                    price=str(tp2),
                    timeInForce="GTC",
                    quantity=q2
                )

        # ---- UPDATE SESSION ----
        day_stats["total"] += 1
        day_stats["symbols"][symbol] = day_stats["symbols"].get(symbol, 0) + 1
        session["stats"][today] = day_stats

        session["trades"].append({
            "time": datetime.utcnow().isoformat(),
            "symbol": symbol,
            "side": side,
            "units": units,
            "leverage": leverage
        })

        session.modified = True

        return {"success": True, "message": f"{side} {symbol} trade placed"}

    except Exception as e:
        return {"success": False, "message": str(e)}
