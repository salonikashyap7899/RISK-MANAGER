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

# ---------------- PRECISION FIX (TRUNCATION METHOD) ----------------
def get_precision(symbol):
    """Fetches exact decimal precision allowed for Price and Quantity."""
    try:
        info = client.futures_exchange_info()
        for s in info['symbols']:
            if s['symbol'] == symbol:
                # Find stepSize for Quantity
                qty_step = next(f['stepSize'] for f in s['filters'] if f['filterType'] == 'LOT_SIZE')
                # Find tickSize for Price
                price_tick = next(f['tickSize'] for f in s['filters'] if f['filterType'] == 'PRICE_FILTER')
                
                # Convert "0.00100" -> 3
                qty_prec = len(qty_step.split('1')[0].split('.')[-1]) if '.' in qty_step else 0
                price_prec = len(price_tick.split('1')[0].split('.')[-1]) if '.' in price_tick else 0
                
                return price_prec, qty_prec
    except:
        return 2, 3 # Defaults

def format_value(value, precision):
    """Truncates value to fixed precision without rounding up."""
    return floor(value * (10**precision)) / (10**precision)

# ---------------- POSITION SIZE + LEVERAGE ----------------
def calculate_position_sizing(unutilized_margin, entry, sl_type, sl_value):
    try:
        if sl_value <= 0 or entry <= 0:
            return {"error": "Invalid SL"}

        risk_amount = unutilized_margin * 0.01
        sl_percent = sl_value if sl_type == "SL % Movement" else (sl_value / entry) * 100
        effective_sl = sl_percent + 0.2

        position_size = (risk_amount / (effective_sl / 100)) / entry
        max_leverage = int(100 / effective_sl)
        max_leverage = max(1, min(max_leverage, 100))

        return {
            "suggested_units": position_size,
            "suggested_leverage": max_leverage,
            "max_leverage": max_leverage,
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

    # 1. Fetch Precisions for this specific symbol
    price_p, qty_p = get_precision(symbol)

    try:
        # 2. Format Quantity (Truncate to avoid floating point junk)
        raw_units = float(user_units) if user_units > 0 else float(sizing["suggested_units"])
        qty = float(f"{{:.{qty_p}f}}".format(raw_units)) 

        # 3. Apply Leverage & Margin
        leverage = int(user_lev) if user_lev > 0 else sizing["max_leverage"]
        client.futures_change_leverage(symbol=symbol, leverage=leverage)
        try:
            client.futures_change_margin_type(symbol=symbol, marginType=margin_mode)
        except: pass

        entry_side = Client.SIDE_BUY if side == "LONG" else Client.SIDE_SELL
        exit_side = Client.SIDE_SELL if side == "LONG" else Client.SIDE_BUY

        # 4. PLACE MAIN MARKET ORDER
        main_order = client.futures_create_order(
            symbol=symbol, 
            side=entry_side, 
            type="MARKET", 
            quantity=qty
        )

        # SUCCESS SO FAR: Log the trade even if SL/TP fails later
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

        # 5. PLACE STOP LOSS (In a separate safety block)
        sl_msg = ""
        try:
            sl_percent = sl_value if sl_type == "SL % Movement" else (sl_value / entry) * 100
            sl_raw = entry * (1 - sl_percent / 100) if side == "LONG" else entry * (1 + sl_percent / 100)
            sl_price = float(f"{{:.{price_p}f}}".format(sl_raw))
            
            client.futures_create_order(
                symbol=symbol, side=exit_side, type="STOP_MARKET",
                stopPrice=sl_price, closePosition=True
            )
            sl_msg = f" | SL set at {sl_price}"
        except Exception as e:
            sl_msg = f" | SL FAILED: {str(e)}"

        # 6. PLACE TAKE PROFIT
        tp_msg = ""
        if tp1 > 0:
            try:
                tp_price = float(f"{{:.{price_p}f}}".format(tp1))
                client.futures_create_order(
                    symbol=symbol, side=exit_side, type="TAKE_PROFIT_MARKET",
                    stopPrice=tp_price, closePosition=True
                )
                tp_msg = f" | TP set at {tp_price}"
            except Exception as e:
                tp_msg = f" | TP FAILED: {str(e)}"

        return {"success": True, "message": f"Main Order Placed!{sl_msg}{tp_msg}"}

    except Exception as e:
        # This only triggers if the MAIN order fails
        return {"success": False, "message": f"Main Order Failed: {str(e)}"}