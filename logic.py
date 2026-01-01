from flask import session
from datetime import datetime
from binance.client import Client
import config

client = Client(config.BINANCE_KEY, config.BINANCE_SECRET)

def initialize_session():
    if "trades" not in session:
        session["trades"] = []
    if "stats" not in session:
        session["stats"] = {}

def get_all_exchange_symbols():
    try:
        info = client.futures_exchange_info()
        return sorted([s["symbol"] for s in info["symbols"] if s["status"] == "TRADING" and s["quoteAsset"] == "USDT"])
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

def adjust_quantity(symbol, qty):
    try:
        info = client.futures_exchange_info()
        for s in info["symbols"]:
            if s["symbol"] == symbol:
                for f in s["filters"]:
                    if f["filterType"] == "LOT_SIZE":
                        step = float(f["stepSize"])
                        precision = len(f["stepSize"].rstrip('0').split('.')[1]) if '.' in f["stepSize"] else 0
                        adjusted = qty - (qty % step)
                        return round(adjusted, precision)
        return round(qty, 3)
    except:
        return round(qty, 3)

def calculate_position_sizing(unutilized_margin, entry, sl_type, sl_value):
    try:
        if sl_value <= 0 or entry <= 0:
            return {"error": "Invalid SL"}
        
        risk_amount = unutilized_margin * 0.01
        perc_dist = sl_value if sl_type == "SL % Movement" else (sl_value / entry) * 100
        
        notional = risk_amount / (perc_dist / 100)
        quantity = notional / entry
        leverage = max(1, min(int(100 / (perc_dist or 1)), 100))

        return {
            "suggested_units": round(quantity, 4),
            "suggested_leverage": leverage,
            "risk_amount": round(risk_amount, 2),
            "error": None
        }
    except Exception as e:
        return {"error": str(e)}

def execute_trade_action(balance, symbol, side, entry, order_type, sl_type, sl_value, sizing, user_units, user_lev, margin_mode, tp1, tp1_pct, tp2):
    today = datetime.utcnow().date().isoformat()
    day_stats = session["stats"].get(today, {"total": 0, "symbols": {}})

    if day_stats["total"] >= 4 or day_stats["symbols"].get(symbol, 0) >= 2:
        return {"success": False, "message": "Daily limit reached"}

    try:
        units = adjust_quantity(symbol, user_units if user_units > 0 else sizing["suggested_units"])
        leverage = int(user_lev) if (user_lev and user_lev != 100) else int(sizing["suggested_leverage"])
        
        client.futures_change_leverage(symbol=symbol, leverage=leverage)
        
        side_binance = Client.SIDE_BUY if side == "LONG" else Client.SIDE_SELL
        
        if order_type == "MARKET":
            client.futures_create_order(symbol=symbol, side=side_binance, type="MARKET", quantity=units)
        else:
            client.futures_create_order(symbol=symbol, side=side_binance, type="LIMIT", price=str(entry), timeInForce="GTC", quantity=units)

        # Update Session Data for Template
        day_stats["total"] += 1
        day_stats["symbols"][symbol] = day_stats["symbols"].get(symbol, 0) + 1
        session["stats"][today] = day_stats
        
        session["trades"].append({
            "timestamp": datetime.utcnow().isoformat(), # Added for index.html
            "symbol": symbol,
            "side": side,
            "entry_price": entry, # Added for index.html
            "units": units,
            "leverage": leverage
        })
        session.modified = True
        return {"success": True, "message": "Trade placed successfully"}
    except Exception as e:
        return {"success": False, "message": str(e)}