from flask import session
from datetime import datetime
from binance.client import Client
import config
import math

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

# ---------------- BINANCE PRECISION (FIXED) ----------------
def get_symbol_filters(symbol):
    info = client.futures_exchange_info()
    for s in info["symbols"]:
        if s["symbol"] == symbol:
            return s["filters"]
    return []

def round_qty(symbol, qty):
    try:
        for f in get_symbol_filters(symbol):
            if f["filterType"] == "LOT_SIZE":
                step_size = f["stepSize"]
                # Calculate precision based on position of '1'
                precision = step_size.find('1') - step_size.find('.')
                if precision < 0: precision = 0
                
                # Manual truncation to avoid float rounding errors
                factor = 10 ** precision
                return math.floor(qty * factor) / factor
        return round(qty, 3)
    except:
        return round(qty, 3)

def round_price(symbol, price):
    try:
        for f in get_symbol_filters(symbol):
            if f["filterType"] == "PRICE_FILTER":
                tick_size = f["tickSize"]
                precision = tick_size.find('1') - tick_size.find('.')
                if precision < 0: precision = 0
                
                factor = 10 ** precision
                return math.floor(price * factor) / factor
        return round(price, 2)
    except:
        return round(price, 2)

# ---------------- POSITION SIZE + LEVERAGE ----------------
def calculate_position_sizing(unutilized_margin, entry, sl_type, sl_value):
    try:
        if sl_value <= 0 or entry <= 0:
            return {"error": "Invalid SL"}

        risk_amount = unutilized_margin * 0.01
        sl_percent = sl_value if sl_type == "SL % Movement" else (sl_value / entry) * 100
        effective_sl = sl_percent + 0.2

        position_size = (risk_amount / effective_sl) * 100
        max_leverage = int(100 / effective_sl)
        max_leverage = max(1, min(max_leverage, 100))

        return {
            "suggested_units": position_size, # Keep raw here, round at execution
            "suggested_leverage": max_leverage,
            "max_leverage": max_leverage,
            "risk_amount": round(risk_amount, 2),
            "error": None
        }
    except Exception as e:
        return {"error": str(e)}

# ---------------- EXECUTE TRADE (FIXED PRECISION) ----------------
def execute_trade_action(
    balance, symbol, side, entry, order_type,
    sl_type, sl_value, sizing,
    user_units, user_lev, margin_mode,
    tp1, tp1_pct, tp2
):
    today = datetime.utcnow().date().isoformat()
    day_stats = session["stats"].get(today, {"total": 0, "symbols": {}})

    if day_stats["total"] >= 4:
        return {"success": False, "message": "Daily limit (4) reached"}

    if day_stats["symbols"].get(symbol, 0) >= 2:
        return {"success": False, "message": f"{symbol} daily limit (2) reached"}

    try:
        # 1. FIXED: Ensure units are float and rounded correctly
        units = float(user_units) if user_units > 0 else float(sizing["suggested_units"])
        qty = round_qty(symbol, units)

        max_lev = sizing["max_leverage"]
        leverage = int(user_lev) if user_lev > 0 else max_lev
        leverage = max(1, min(leverage, max_lev))

        client.futures_change_leverage(symbol=symbol, leverage=leverage)
        # Set Margin Mode (Isolated/Cross)
        try:
            client.futures_change_margin_type(symbol=symbol, marginType=margin_mode)
        except:
            pass # Ignore if already set

        entry_side = Client.SIDE_BUY if side == "LONG" else Client.SIDE_SELL
        exit_side = Client.SIDE_SELL if side == "LONG" else Client.SIDE_BUY

        # 2. MAIN ORDER
        client.futures_create_order(
            symbol=symbol,
            side=entry_side,
            type="MARKET",
            quantity=qty
        )

        # 3. FIXED: SL PRICE PRECISION
        sl_percent = sl_value if sl_type == "SL % Movement" else (sl_value / entry) * 100
        sl_price_raw = entry * (1 - sl_percent / 100) if side == "LONG" else entry * (1 + sl_percent / 100)
        sl_price = round_price(symbol, sl_price_raw)

        client.futures_create_order(
            symbol=symbol,
            side=exit_side,
            type="STOP_MARKET",
            stopPrice=sl_price,
            closePosition=True
        )

        # 4. FIXED: TP PRICE PRECISION
        if tp1 > 0:
            tp_price = round_price(symbol, tp1)
            client.futures_create_order(
                symbol=symbol,
                side=exit_side,
                type="TAKE_PROFIT_MARKET",
                stopPrice=tp_price,
                closePosition=True
            )

        # Update Session Stats
        day_stats["total"] += 1
        day_stats["symbols"][symbol] = day_stats["symbols"].get(symbol, 0) + 1
        session["stats"][today] = day_stats

        session["trades"].append({
            "time": datetime.utcnow().isoformat(),
            "symbol": symbol,
            "side": side,
            "units": qty,
            "leverage": leverage
        })
        session.modified = True

        return {"success": True, "message": f"Trade placed! Qty: {qty}, SL: {sl_price}"}

    except Exception as e:
        return {"success": False, "message": str(e)}