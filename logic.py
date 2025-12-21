# logic.py
from flask import session
from datetime import datetime
from math import ceil
from binance.client import Client
import config

# Initialize Binance Client
client = Client(config.BINANCE_KEY, config.BINANCE_SECRET)

def initialize_session():
    if "trades" not in session:
        session["trades"] = []
    if "stats" not in session:
        session["stats"] = {}

def get_binance_data():
    """Fetches real account balance and margin from Binance Futures."""
    try:
        acc = client.futures_account()
        balance = float(acc.get('totalWalletBalance', 0))
        margin = float(acc.get('totalInitialMargin', 0))
        return balance, margin
    except Exception as e:
        print(f"Binance API Error: {e}")
        return config.TOTAL_CAPITAL_DEFAULT, 0.0

def calculate_position_sizing(balance, entry, sl_type, sl_value):
    """The Proprietary Sizing Logic (Kept in Backend)"""
    try:
        entry = float(entry)
        sl_value = float(sl_value)
        risk_amount = balance * (config.RISK_PERCENT / 100.0)

        # Your unique logic with buffers
        if sl_type == "SL Points":
            sl_distance = sl_value + 20 
            suggested_units = risk_amount / sl_distance
            suggested_lev = (suggested_units * entry) / balance if balance > 0 else 0
        else:
            sl_distance = sl_value + 0.2
            suggested_units = risk_amount / ((sl_distance / 100) * entry)
            suggested_lev = 100 / sl_value

        return {
            "suggested_units": round(suggested_units, 4),
            "suggested_leverage": ceil(suggested_lev * 2) / 2,
            "risk_amount": round(risk_amount, 2),
            "error": None if sl_value > 0 else "SL Required"
        }
    except:
        return {"error": "Invalid Input Data"}

def execute_trade_action(symbol, side, entry, sl_type, sl_value, tp_list, sizing, user_units, user_lev):
    """Executes real trades on Binance."""
    today = datetime.utcnow().date().isoformat()
    stats = session.get("stats", {}).get(today, {"total": 0, "by_symbol": {}})

    # Limit Checks
    if stats["total"] >= config.DAILY_MAX_TRADES:
        return {"success": False, "message": "Daily Limit Reached"}
    
    # Binance uses USDT pairs (e.g., BTCUSDT)
    binance_symbol = symbol.replace("USD", "USDT")
    
    try:
        units = float(user_units) if float(user_units) > 0 else sizing["suggested_units"]
        leverage = int(user_lev) if float(user_lev) > 0 else int(sizing["suggested_leverage"])

        # 1. Update Leverage on Binance
        client.futures_change_leverage(symbol=binance_symbol, leverage=leverage)

        # 2. Main Market Order
        order_side = Client.SIDE_BUY if side == "LONG" else Client.SIDE_SELL
        main_order = client.futures_create_order(
            symbol=binance_symbol,
            side=order_side,
            type=Client.FUTURE_ORDER_TYPE_MARKET,
            quantity=round(units, 3) 
        )

        # 3. Stop Loss Order
        stop_side = Client.SIDE_SELL if side == "LONG" else Client.SIDE_BUY
        if sl_type == "SL Points":
            sl_price = entry - sl_value if side == "LONG" else entry + sl_value
        else:
            sl_price = entry * (1 - sl_value/100) if side == "LONG" else entry * (1 + sl_value/100)

        client.futures_create_order(
            symbol=binance_symbol,
            side=stop_side,
            type=Client.FUTURE_ORDER_TYPE_STOP_MARKET,
            stopPrice=round(sl_price, 2),
            closePosition=True
        )

        # 4. Take Profit Orders
        for tp in tp_list:
            if tp['price'] > 0:
                client.futures_create_order(
                    symbol=binance_symbol,
                    side=stop_side,
                    type=Client.FUTURE_ORDER_TYPE_TAKE_PROFIT_MARKET,
                    stopPrice=round(tp['price'], 2),
                    quantity=round(units * (tp['percent_position'] / 100), 3)
                )

        # Log for UI
        session["trades"].append({
            "timestamp": datetime.utcnow().isoformat(),
            "date": today,
            "symbol": symbol,
            "side": side,
            "entry_price": entry,
            "stop_loss": sl_value,
            "sl_mode": sl_type,
            "units": units,
            "leverage": leverage,
            "risk_usd": sizing["risk_amount"]
        })
        
        # Update Session Stats
        if today not in session["stats"]: session["stats"][today] = {"total": 0, "by_symbol": {}}
        session["stats"][today]["total"] += 1
        session["stats"][today]["by_symbol"][symbol] = session["stats"][today]["by_symbol"].get(symbol, 0) + 1
        session.modified = True

        return {"success": True, "message": f"SUCCESS: {side} {symbol} Executed on Binance"}

    except Exception as e:
        return {"success": False, "message": f"BINANCE ERROR: {str(e)}"}