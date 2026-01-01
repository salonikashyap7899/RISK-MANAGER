from flask import session
from datetime import datetime
from binance.client import Client
import config

# ---------------- BINANCE CLIENT ----------------
client = Client(config.BINANCE_KEY, config.BINANCE_SECRET)

# ---------------- SESSION INIT ----------------
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

# ---------------- QUANTITY PRECISION FIX ----------------
def adjust_quantity(symbol, qty):
    try:
        info = client.futures_exchange_info()
        for s in info["symbols"]:
            if s["symbol"] == symbol:
                for f in s["filters"]:
                    if f["filterType"] == "LOT_SIZE":
                        step = f["stepSize"]
                        if "." in step:
                            precision = len(step.rstrip("0").split(".")[1])
                        else:
                            precision = 0
                        step = float(step)
                        adjusted = qty - (qty % step)
                        return round(adjusted, precision)
        return round(qty, 3)
    except:
        return round(qty, 3)

# ---------------- POSITION SIZE & LEVERAGE ----------------
def calculate_position_sizing(unutilized_margin, entry, sl_type, sl_value):
    try:
        if sl_value <= 0 or entry <= 0:
            return {"error": "Invalid SL or entry"}

        # 1% fixed risk
        risk_amount = unutilized_margin * 0.01

        # SL % movement + buffer
        effective_sl = sl_value + 0.2

        # Position size (USDT notional)
        notional = (risk_amount / effective_sl) * 100

        # Quantity
        quantity = notional / entry

        # Suggested leverage
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
    # ✅ SESSION SAFETY (NO 500 ERROR)
    if "stats" not in session:
        session["stats"] = {}
    if "trades" not in session:
        session["trades"] = []

    # ✅ SIZE SAFETY
    if not sizing or sizing.get("error"):
        return {"success": False, "message": "Invalid SL or sizing"}

    today = datetime.utcnow().date().isoformat()
    day_stats = session["stats"].get(today, {"total": 0, "symbols": {}})

    # Daily trade limits
    if day_stats["total"] >= 4:
        return {"success": False, "message": "Daily trade limit reached (4)"}

    if day_stats["symbols"].get(symbol, 0) >= 2:
        return {"success": False, "message": f"{symbol} daily limit reached (2)"}

    try:
        # Quantity
        raw_units = user_units if user_units > 0 else sizing["suggested_units"]
        units = adjust_quantity(symbol, raw_units)

        if units <= 0:
            return {"success": False, "message": "Order size too small"}

        # Leverage logic (NO override bug)
        if user_lev and user_lev != 100:
            leverage = int(user_lev)
        else:
            leverage = int(sizing["suggested_leverage"])

        leverage = max(1, min(leverage, 100))

        # Margin mode
        try:
            client.futures_change_margin_type(
                symbol=symbol,
                marginType=margin_mode.upper()
            )
        except:
            pass

        client.futures_change_leverage(symbol=symbol, leverage=leverage)

        side_binance = Client.SIDE_BUY if side == "LONG" else Client.SIDE_SELL

        # Order placement
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

        # Update stats
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

        return {"success": True, "message": "Trade placed successfully"}

    except Exception as e:
        return {"success": False, "message": str(e)}
