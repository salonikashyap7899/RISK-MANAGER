from flask import session
from datetime import datetime
from binance.client import Client
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
        return 0.0, 0.0

def get_live_price(symbol):
    try:
        return float(client.futures_symbol_ticker(symbol=symbol)["price"])
    except:
        return 0.0

def get_precision(symbol):
    info = client.futures_exchange_info()
    for s in info["symbols"]:
        if s["symbol"] == symbol:
            qty_step = next(f["stepSize"] for f in s["filters"] if f["filterType"] == "LOT_SIZE")
            price_tick = next(f["tickSize"] for f in s["filters"] if f["filterType"] == "PRICE_FILTER")
            qty_p = len(qty_step.split(".")[1].rstrip("0")) if "." in qty_step else 0
            price_p = len(price_tick.split(".")[1].rstrip("0")) if "." in price_tick else 0
            return price_p, qty_p
    return 2, 3

# ---------------- POSITION SIZING ----------------
def calculate_position_sizing(unutilized, entry, sl_type, sl_value):
    if entry <= 0 or sl_value <= 0:
        return {"error": "Invalid SL"}

    risk = unutilized * 0.01
    sl_percent = sl_value if sl_type == "SL % Movement" else (sl_value / entry) * 100
    sl_percent += 0.2  # buffer

    units = (risk / (sl_percent / 100)) / entry
    leverage = max(1, min(int(100 / sl_percent), 100))

    return {
        "suggested_units": round(units, 4),
        "suggested_leverage": leverage,
        "max_leverage": leverage,
        "risk_amount": round(risk, 2),
        "error": None
    }

# ---------------- EXECUTE TRADE ----------------
def execute_trade_action(balance, symbol, side, entry, sl_type, sl_value,
                         sizing, user_units, user_lev, margin_mode):

    today = datetime.utcnow().date().isoformat()
    stats = session["stats"].get(today, {"total": 0})

    if stats["total"] >= 4:
        return {"success": False, "message": "Daily trade limit reached"}

    try:
        price_p, qty_p = get_precision(symbol)

        qty = user_units if user_units > 0 else sizing["suggested_units"]
        qty = float(f"{qty:.{qty_p}f}")

        lev = user_lev if user_lev > 0 else sizing["max_leverage"]

        client.futures_change_leverage(symbol=symbol, leverage=int(lev))
        try:
            client.futures_change_margin_type(symbol=symbol, marginType=margin_mode)
        except:
            pass

        buy = Client.SIDE_BUY if side == "LONG" else Client.SIDE_SELL
        sell = Client.SIDE_SELL if side == "LONG" else Client.SIDE_BUY

        # MAIN ORDER
        client.futures_create_order(
            symbol=symbol,
            side=buy,
            type="MARKET",
            quantity=qty
        )

        # LOG TRADE (FIXED)
        session["trades"].append({
            "time": datetime.utcnow().strftime("%H:%M:%S"),
            "symbol": symbol,
            "side": side,
            "units": qty,
            "leverage": lev
        })

        stats["total"] += 1
        session["stats"][today] = stats
        session.modified = True

        # STOP LOSS
        sl_percent = sl_value if sl_type == "SL % Movement" else (sl_value / entry) * 100
        sl_price = entry * (1 - sl_percent / 100) if side == "LONG" else entry * (1 + sl_percent / 100)
        sl_price = float(f"{sl_price:.{price_p}f}")

        client.futures_create_order(
            symbol=symbol,
            side=sell,
            type="STOP_MARKET",
            stopPrice=sl_price,
            closePosition=True,
            workingType="MARK_PRICE"
        )

        return {"success": True, "message": f"Order placed | Qty: {qty} | SL: {sl_price}"}

    except Exception as e:
        return {"success": False, "message": str(e)}
