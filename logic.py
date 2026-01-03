from flask import session
from datetime import datetime
from binance.client import Client
import config
import math

client = Client(config.BINANCE_KEY, config.BINANCE_SECRET)

# ---------------- SESSION ----------------
def initialize_session():
    session.setdefault("trades", [])
    session.setdefault("stats", {})

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

# ---------------- PRECISION ----------------
def get_symbol_filters(symbol):
    info = client.futures_exchange_info()
    for s in info["symbols"]:
        if s["symbol"] == symbol:
            return s["filters"]
    return []

def round_qty(symbol, qty):
    for f in get_symbol_filters(symbol):
        if f["filterType"] == "LOT_SIZE":
            step = float(f["stepSize"])
            if step == 1:
                return int(qty)
            precision = abs(int(round(-math.log10(step))))
            return round(qty - (qty % step), precision)
    return qty

def round_price(symbol, price):
    for f in get_symbol_filters(symbol):
        if f["filterType"] == "PRICE_FILTER":
            tick = float(f["tickSize"])
            if tick == 1:
                return int(price)
            precision = abs(int(round(-math.log10(tick))))
            return round(price - (price % tick), precision)
    return price

# ---------------- POSITION SIZING ----------------
def calculate_position_sizing(unutilized_margin, entry, sl_type, sl_value):
    if entry <= 0:
        return {"error": "Invalid Entry"}

    risk_amount = unutilized_margin * 0.01

    if sl_value > 0:
        sl_percent = sl_value if sl_type == "SL % Movement" else (sl_value / entry) * 100
        effective_sl = sl_percent + 0.2
    else:
        effective_sl = 1.2  # fallback safety

    position_size = (risk_amount / effective_sl) * 100
    max_lev = max(1, min(int(100 / effective_sl), 100))

    return {
        "suggested_units": round(position_size, 3),
        "suggested_leverage": max_lev,
        "max_leverage": max_lev,
        "risk_amount": round(risk_amount, 2),
        "error": None
    }

# ---------------- EXECUTE TRADE ----------------
def execute_trade_action(
    balance, symbol, side, entry, order_type,
    sl_type, sl_value, sizing,
    user_units, user_lev, margin_mode,
    tp1, tp1_pct, tp2
):
    today = datetime.utcnow().date().isoformat()
    stats = session["stats"].get(today, {"total": 0, "symbols": {}})

    if stats["total"] >= 4:
        return {"success": False, "message": "Daily limit reached"}

    if stats["symbols"].get(symbol, 0) >= 2:
        return {"success": False, "message": "Symbol limit reached"}

    try:
        units = user_units if user_units > 0 else sizing["suggested_units"]
        qty = round_qty(symbol, units)

        leverage = int(user_lev) if user_lev > 0 else sizing["max_leverage"]
        leverage = max(1, min(leverage, sizing["max_leverage"]))

        client.futures_change_leverage(symbol=symbol, leverage=leverage)

        entry_side = Client.SIDE_BUY if side == "LONG" else Client.SIDE_SELL
        exit_side = Client.SIDE_SELL if side == "LONG" else Client.SIDE_BUY

        # ---------- ENTRY ORDER ----------
        client.futures_create_order(
            symbol=symbol,
            side=entry_side,
            type="MARKET",
            quantity=qty
        )

        # ---------- LIVE LOG (ALWAYS) ----------
        session["trades"].append({
            "time": datetime.utcnow().isoformat(),
            "symbol": symbol,
            "side": side,
            "units": qty,
            "leverage": leverage
        })
        session.modified = True

        # ---------- STOP LOSS (ONLY IF SL > 0) ----------
        if sl_value > 0:
            sl_percent = sl_value if sl_type == "SL % Movement" else (sl_value / entry) * 100
            mark = float(client.futures_mark_price(symbol=symbol)["markPrice"])

            sl_price = (
                mark * (1 - sl_percent / 100)
                if side == "LONG"
                else mark * (1 + sl_percent / 100)
            )

            sl_price = round_price(symbol, sl_price)

            try:
                client.futures_create_order(
                    symbol=symbol,
                    side=exit_side,
                    type="STOP_MARKET",
                    stopPrice=sl_price,
                    closePosition=True
                )
            except:
                pass

        stats["total"] += 1
        stats["symbols"][symbol] = stats["symbols"].get(symbol, 0) + 1
        session["stats"][today] = stats

        return {"success": True, "message": "Order placed successfully"}

    except Exception as e:
        return {"success": False, "message": str(e)}
