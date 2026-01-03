from flask import session
from datetime import datetime
from binance.client import Client
import config
import math

client = Client(config.BINANCE_KEY, config.BINANCE_SECRET)

def initialize_session():
    if "trades" not in session: session["trades"] = []
    if "stats" not in session: session["stats"] = {}

def get_all_exchange_symbols():
    try:
        info = client.futures_exchange_info()
        return sorted([s["symbol"] for s in info["symbols"] if s["status"] == "TRADING" and s["quoteAsset"] == "USDT"])
    except: return ["BTCUSDT", "ETHUSDT"]

def get_live_balance():
    try:
        acc = client.futures_account()
        return float(acc["totalWalletBalance"]), float(acc["totalInitialMargin"])
    except: return None, None

def get_live_price(symbol):
    try: return float(client.futures_symbol_ticker(symbol=symbol)["price"])
    except: return None

# --- IMPROVED PRECISION FETCHING ---
def get_precision(symbol):
    info = client.futures_exchange_info()
    for s in info['symbols']:
        if s['symbol'] == symbol:
            qty_step = next(f['stepSize'] for f in s['filters'] if f['filterType'] == 'LOT_SIZE')
            price_tick = next(f['tickSize'] for f in s['filters'] if f['filterType'] == 'PRICE_FILTER')
            qty_p = len(qty_step.split('1')[0].split('.')[-1]) if '.' in qty_step else 0
            price_p = len(price_tick.split('1')[0].split('.')[-1]) if '.' in price_tick else 0
            return price_p, qty_p
    return 2, 3

def calculate_position_sizing(unutilized_margin, entry, sl_type, sl_value):
    try:
        if sl_value <= 0 or entry <= 0: return {"error": "Invalid SL"}
        risk_pool = unutilized_margin * 0.01
        sl_percent = sl_value if sl_type == "SL % Movement" else (sl_value / entry) * 100
        effective_sl = sl_percent + 0.2
        pos_size = (risk_pool / (effective_sl / 100)) / entry
        max_lev = max(1, min(int(100 / effective_sl), 100))
        return {"suggested_units": pos_size, "suggested_leverage": max_lev, "max_leverage": max_lev, "risk_amount": round(risk_pool, 2), "error": None}
    except: return {"error": "Calc Error"}

def execute_trade_action(balance, symbol, side, entry, order_type, sl_type, sl_value, sizing, user_units, user_lev, margin_mode):
    today = datetime.utcnow().date().isoformat()
    day_stats = session["stats"].get(today, {"total": 0, "symbols": {}})
    if day_stats["total"] >= 4: return {"success": False, "message": "Daily limit reached"}

    try:
        price_p, qty_p = get_precision(symbol)
        
        # 1. Format Quantity (Fixed -1111 Precision Error)
        raw_qty = float(user_units) if user_units > 0 else float(sizing["suggested_units"])
        qty = float(f"{{:.{qty_p}f}}".format(raw_qty))
        
        # 2. Settings
        leverage = int(user_lev) if user_lev > 0 else sizing["max_leverage"]
        client.futures_change_leverage(symbol=symbol, leverage=leverage)
        try: client.futures_change_margin_type(symbol=symbol, marginType=margin_mode)
        except: pass

        e_side = Client.SIDE_BUY if side == "LONG" else Client.SIDE_SELL
        x_side = Client.SIDE_SELL if side == "LONG" else Client.SIDE_BUY

        # 3. Main Order
        client.futures_create_order(symbol=symbol, side=e_side, type="MARKET", quantity=qty)

        # 4. FIXED: Log trade immediately after main order succeeds
        session["trades"].append({
            "time": datetime.utcnow().strftime('%H:%M:%S'),
            "symbol": symbol,
            "side": side,
            "units": qty,
            "leverage": leverage
        })
        day_stats["total"] += 1
        session["stats"][today] = day_stats
        session.modified = True

        # 5. FIXED: SL Order (Fixed -4120 Algo Order Error)
        sl_percent = sl_value if sl_type == "SL % Movement" else (sl_value / entry) * 100
        sl_raw = entry * (1 - sl_percent / 100) if side == "LONG" else entry * (1 + sl_percent / 100)
        sl_p = float(f"{{:.{price_p}f}}".format(sl_raw))

        client.futures_create_order(
            symbol=symbol, side=x_side, type="STOP_MARKET", 
            stopPrice=sl_p, closePosition=True, 
            timeInForce="GTC", workingType="MARK_PRICE"
        )

        return {"success": True, "message": f"Success! Qty: {qty}, SL: {sl_p}"}
    except Exception as e:
        return {"success": False, "message": str(e)}