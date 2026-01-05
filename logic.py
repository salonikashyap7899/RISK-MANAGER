from flask import session
from datetime import datetime
from binance.client import Client
import config
import math

# Lazy initialization to avoid errors on import
_client = None

def get_client():
    global _client
    if _client is None:
        try:
            _client = Client(config.BINANCE_KEY, config.BINANCE_SECRET)
        except Exception as e:
            print(f"Warning: Could not initialize Binance client: {e}")
            _client = None
    return _client

# ---------------- SESSION ----------------
def initialize_session():
    if "trades" not in session:
        session["trades"] = []
    if "stats" not in session:
        session["stats"] = {}

# ---------------- BINANCE HELPERS ----------------
def get_all_exchange_symbols():
    try:
        client = get_client()
        if client is None:
            return ["BTCUSDT", "ETHUSDT"]
        info = client.futures_exchange_info()
        return sorted([
            s["symbol"] for s in info["symbols"]
            if s["status"] == "TRADING" and s["quoteAsset"] == "USDT"
        ])
    except:
        return ["BTCUSDT", "ETHUSDT"]

def get_live_balance():
    try:
        client = get_client()
        if client is None:
            return None, None
        acc = client.futures_account()
        return float(acc["totalWalletBalance"]), float(acc["totalInitialMargin"])
    except:
        return None, None

def get_live_price(symbol):
    try:
        client = get_client()
        if client is None:
            return None
        return float(client.futures_symbol_ticker(symbol=symbol)["price"])
    except:
        return None

# ---------------- PRECISION ----------------
def get_symbol_filters(symbol):
    client = get_client()
    if client is None:
        return []
    info = client.futures_exchange_info()
    for s in info["symbols"]:
        if s["symbol"] == symbol:
            return s["filters"]
    return []

def get_lot_step(symbol):
    for f in get_symbol_filters(symbol):
        if f["filterType"] == "LOT_SIZE":
            return float(f["stepSize"])
    return 0.001

def round_qty(symbol, qty):
    step = get_lot_step(symbol)
    if step == 1:
        return max(1, int(qty))

    precision = abs(int(round(-math.log10(step))))
    rounded = round(qty - (qty % step), precision)

    return rounded if rounded > 0 else step

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
        return {"error": "Invalid Entry", "suggested_units": 0, "suggested_leverage": 1, "risk_amount": 0}

    risk_amount = unutilized_margin * 0.01

    if sl_value > 0:
        if sl_type == "SL % Movement":
            sl_percent = sl_value
            sl_distance = entry * (sl_value / 100)
        else:
            sl_distance = abs(entry - sl_value)
            sl_percent = (sl_distance / entry) * 100

        if sl_distance <= 0:
            return {"error": "Invalid SL distance", "suggested_units": 0, "suggested_leverage": 1, "risk_amount": 0}

        calculated_leverage = 100 / (sl_percent + 0.2)
        max_leverage = min(int(calculated_leverage), 125)
        position_size = (risk_amount * max_leverage) / sl_distance
    else:
        max_leverage = 10
        position_size = risk_amount / entry

    return {
        "suggested_units": round(position_size, 6),
        "suggested_leverage": max_leverage,
        "max_leverage": max_leverage,
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
        client = get_client()
        if client is None:
            return {"success": False, "message": "Binance client connection error"}

        # FIX: Ensure units are pulled correctly from sizing if user_units is 0 or None
        final_units = float(user_units) if (user_units and float(user_units) > 0) else sizing["suggested_units"]
        qty = round_qty(symbol, final_units)

        final_leverage = int(user_lev) if (user_lev and int(user_lev) > 0) else sizing["max_leverage"]
        final_leverage = max(1, min(final_leverage, 125))

        client.futures_change_leverage(symbol=symbol, leverage=final_leverage)

        entry_side = Client.SIDE_BUY if side == "LONG" else Client.SIDE_SELL
        exit_side = Client.SIDE_SELL if side == "LONG" else Client.SIDE_BUY

        # -------- ENTRY --------
        entry_order = client.futures_create_order(
            symbol=symbol,
            side=entry_side,
            type="MARKET",
            quantity=qty
        )
        
        mark = float(client.futures_mark_price(symbol=symbol)["markPrice"])
        actual_entry = mark
        notional = qty * actual_entry

        # -------- STOP LOSS --------
        sl_price_value = None
        if sl_value > 0:
            sl_percent = sl_value if sl_type == "SL % Movement" else abs(entry - sl_value) / entry * 100
            sl_price = actual_entry * (1 - sl_percent / 100) if side == "LONG" else actual_entry * (1 + sl_percent / 100)
            sl_price = round_price(symbol, sl_price)
            sl_price_value = sl_price

            client.futures_create_order(
                symbol=symbol, side=exit_side, type="STOP_MARKET",
                stopPrice=sl_price, closePosition=True
            )

        # -------- TAKE PROFIT --------
        tp1_price_value = tp1 if tp1 > 0 else None
        tp2_price_value = tp2 if tp2 > 0 else None
        
        if tp1 > 0 and tp1_pct > 0:
            client.futures_create_order(
                symbol=symbol, side=exit_side, type="TAKE_PROFIT_MARKET",
                stopPrice=round_price(symbol, tp1), quantity=round_qty(symbol, qty * (tp1_pct / 100))
            )
        
        if tp2 > 0:
            if tp1 > 0 and tp1_pct > 0:
                tp2_qty = round_qty(symbol, qty * ((100 - tp1_pct) / 100))
                client.futures_create_order(
                    symbol=symbol, side=exit_side, type="TAKE_PROFIT_MARKET",
                    stopPrice=round_price(symbol, tp2), quantity=tp2_qty
                )
            else:
                client.futures_create_order(
                    symbol=symbol, side=exit_side, type="TAKE_PROFIT_MARKET",
                    stopPrice=round_price(symbol, tp2), closePosition=True
                )

        # -------- LOGGING --------
        new_trade = {
            "time": datetime.utcnow().strftime("%H:%M:%S"),
            "symbol": symbol,
            "side": side,
            "units": qty,
            "leverage": final_leverage,
            "entry": actual_entry,
            "sl": sl_price_value,
            "status": "OPEN"
        }
        
        trades = session.get("trades", [])
        trades.append(new_trade)
        session["trades"] = trades
        
        stats["total"] += 1
        stats["symbols"][symbol] = stats["symbols"].get(symbol, 0) + 1
        session["stats"][today] = stats
        session.modified = True

        return {"success": True, "message": f"Order Successful: {qty} {symbol}"}

    except Exception as e:
        return {"success": False, "message": str(e)}