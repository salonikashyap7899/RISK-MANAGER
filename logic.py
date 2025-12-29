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
        return None, None

def get_live_price(symbol):
    try:
        return float(client.futures_symbol_ticker(symbol=symbol)["price"])
    except:
        return None

# ---------------- POSITION SIZE + LEVERAGE ----------------
def calculate_position_sizing(unutilized_margin, entry, sl_type, sl_value):
    try:
        if sl_value <= 0 or entry <= 0:
            return {"error": "Invalid SL or Entry"}

        # 1% Risk of unutilized margin
        risk_amount = unutilized_margin * 0.01

        # Convert SL to percentage movement
        if sl_type == "% Movement":
            sl_percent = sl_value
        else:  # SL Points
            sl_percent = (sl_value / entry) * 100

        if sl_percent <= 0:
            return {"error": "SL percentage must be > 0"}

        # Effective SL with buffer
        effective_sl = sl_percent + 0.2  # +0.2% buffer

        # FIXED POSITION SIZE FORMULA (as requested)
        position_size = (risk_amount / effective_sl) * 100

        # SUGGESTED LEVERAGE: max 100, based on effective SL
        leverage = 100 / effective_sl
        leverage = max(1, min(round(leverage), 100))  # Clamp between 1 and 100

        return {
            "suggested_units": round(position_size, 3),
            "suggested_leverage": leverage,
            "risk_amount": round(risk_amount, 2),
            "effective_sl_percent": round(effective_sl, 3),
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

    # ---- DAILY LIMITS ----
    if day_stats["total"] >= 4:
        return {"success": False, "message": "❌ Daily trade limit (4) reached"}

    if day_stats["symbols"].get(symbol, 0) >= 2:
        return {"success": False, "message": f"❌ {symbol} daily limit (2) reached"}

    try:
        units = user_units if user_units > 0 else sizing["suggested_units"]
        leverage = user_lev if user_lev > 0 else sizing["suggested_leverage"]
        leverage = max(1, min(int(leverage), 100))

        # Set Margin Type (Isolated/Cross)
        try:
            client.futures_change_margin_type(
                symbol=symbol,
                marginType=margin_mode.upper()
            )
        except:
            pass  # Already set or error (ignore)

        # Set Leverage
        client.futures_change_leverage(symbol=symbol, leverage=leverage)

        order_side = Client.SIDE_BUY if side == "LONG" else Client.SIDE_SELL

        # MAIN ENTRY ORDER
        quantity = abs(round(units, 3))
        if order_type == "MARKET":
            client.futures_create_order(
                symbol=symbol,
                side=order_side,
                type="MARKET",
                quantity=quantity
            )
        else:
            client.futures_create_order(
                symbol=symbol,
                side=order_side,
                type="LIMIT",
                price=str(entry),
                timeInForce="GTC",
                quantity=quantity
            )

        # TAKE PROFIT ORDERS
        tp_side = Client.SIDE_SELL if side == "LONG" else Client.SIDE_BUY

        remaining_units = units

        if tp1 > 0 and tp1_pct > 0:
            q1 = abs(round(units * (tp1_pct / 100), 3))
            q1 = min(q1, remaining_units)  # Safety
            client.futures_create_order(
                symbol=symbol,
                side=tp_side,
                type="LIMIT",
                price=str(tp1),
                timeInForce="GTC",
                quantity=q1
            )
            remaining_units -= q1

        if tp2 > 0 and remaining_units > 0.001:  # Avoid dust
            q2 = abs(round(remaining_units, 3))
            client.futures_create_order(
                symbol=symbol,
                side=tp_side,
                type="LIMIT",
                price=str(tp2),
                timeInForce="GTC",
                quantity=q2
            )

        # UPDATE SESSION STATS
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

        return {"success": True, "message": f"✅ {side} {symbol} trade placed"}

    except Exception as e:
        return {"success": False, "message": f"Binance Error: {str(e)}"}