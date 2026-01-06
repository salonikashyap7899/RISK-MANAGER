from flask import session
from datetime import datetime
from binance.client import Client
from binance.exceptions import BinanceAPIException
import config
import math
import traceback

_client = None

def get_client():
    global _client
    if _client is None:
        try:
            _client = Client(config.BINANCE_KEY, config.BINANCE_SECRET)
            # Test connection
            _client.futures_account()
            print("✅ Binance client initialized successfully")
        except Exception as e:
            print(f"❌ Error initializing Binance client: {e}")
            _client = None
    return _client

def initialize_session():
    if "trades" not in session:
        session["trades"] = []
    if "stats" not in session:
        session["stats"] = {}
    session.modified = True

def get_all_exchange_symbols():
    try:
        client = get_client()
        if client is None: return ["BTCUSDT", "ETHUSDT"]
        info = client.futures_exchange_info()
        return sorted([s["symbol"] for s in info["symbols"] if s["status"] == "TRADING" and s["quoteAsset"] == "USDT"])
    except:
        return ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"]

def get_live_balance():
    try:
        client = get_client()
        if client is None: return None, None
        acc = client.futures_account()
        return float(acc["totalWalletBalance"]), float(acc["totalInitialMargin"])
    except Exception as e:
        print(f"Error getting balance: {e}")
        return None, None

def get_live_price(symbol):
    try:
        client = get_client()
        if client is None: return None
        return float(client.futures_symbol_ticker(symbol=symbol)["price"])
    except Exception as e:
        print(f"Error getting price for {symbol}: {e}")
        return None

def get_symbol_filters(symbol):
    try:
        client = get_client()
        if client is None: return []
        info = client.futures_exchange_info()
        for s in info["symbols"]:
            if s["symbol"] == symbol: return s["filters"]
    except:
        pass
    return []

def get_lot_step(symbol):
    for f in get_symbol_filters(symbol):
        if f["filterType"] == "LOT_SIZE": 
            return float(f["stepSize"])
    return 0.001

def round_qty(symbol, qty):
    step = get_lot_step(symbol)
    if step == 0:
        step = 0.001
    if step >= 1:
        return max(1, int(qty))
    precision = abs(int(round(-math.log10(step))))
    rounded = round(qty - (qty % step), precision)
    return rounded if rounded > 0 else step

def round_price(symbol, price):
    for f in get_symbol_filters(symbol):
        if f["filterType"] == "PRICE_FILTER":
            tick = float(f["tickSize"])
            if tick == 0:
                return price
            if tick >= 1:
                return int(price)
            precision = abs(int(round(-math.log10(tick))))
            return round(price - (price % tick), precision)
    return round(price, 2)

def calculate_position_sizing(unutilized_margin, entry, sl_type, sl_value):
    if entry <= 0: 
        return {"error": "Invalid Entry"}
    
    risk_amount = unutilized_margin * 0.01

    if sl_value > 0:
        if sl_type == "SL % Movement":
            sl_percent = sl_value
            sl_distance = entry * (sl_value / 100)
        else:
            sl_distance = abs(entry - sl_value)
            sl_percent = (sl_distance / entry) * 100

        if sl_distance <= 0: 
            return {"error": "Invalid SL distance"}

        # Leverage Formula: 100 / (SL% + 0.2)
        calculated_leverage = 100 / (sl_percent + 0.2)
        max_leverage = min(int(calculated_leverage), 125)
        
        # Position Value Formula: [Risk ÷ (SL% + 0.2)] × 100
        pos_value_usdt = (risk_amount / (sl_percent + 0.2)) * 100
        position_size = pos_value_usdt / entry
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

def execute_trade_action(
    balance, symbol, side, entry, order_type,
    sl_type, sl_value, sizing,
    user_units, user_lev, margin_mode,
    tp1, tp1_pct, tp2
):
    today = datetime.utcnow().date().isoformat()
    stats = session.get("stats", {}).get(today, {"total": 0, "symbols": {}})

    if stats["total"] >= 4:
        return {"success": False, "message": "❌ Daily limit reached (4 trades)"}

    try:
        client = get_client()
        if client is None:
            return {"success": False, "message": "❌ Binance client not connected"}
            
        # Calculate quantities
        units = user_units if user_units > 0 else sizing["suggested_units"]
        qty = round_qty(symbol, units)

        # Set leverage
        leverage = int(user_lev) if user_lev > 0 else sizing["max_leverage"]
        client.futures_change_leverage(symbol=symbol, leverage=leverage)
        print(f"✅ Leverage set to {leverage}x for {symbol}")

        # Set margin mode
        try:
            client.futures_change_margin_type(symbol=symbol, marginType=margin_mode)
            print(f"✅ Margin mode set to {margin_mode}")
        except BinanceAPIException as e:
            if "No need to change margin type" not in str(e):
                print(f"⚠️ Margin mode warning: {e}")

        entry_side = Client.SIDE_BUY if side == "LONG" else Client.SIDE_SELL
        exit_side = Client.SIDE_SELL if side == "LONG" else Client.SIDE_BUY

        # -------- ENTRY ORDER --------
        print(f"\n📊 Placing {side} order: {qty} {symbol} @ market price")
        entry_order = client.futures_create_order(
            symbol=symbol,
            side=entry_side,
            type="MARKET",
            quantity=qty
        )
        print(f"✅ Entry order placed: {entry_order['orderId']}")
        
        # Get actual entry price
        mark = float(client.futures_mark_price(symbol=symbol)["markPrice"])
        actual_entry = mark
        print(f"📍 Entry price: {actual_entry}")

        # -------- STOP LOSS ORDER --------
        sl_price_value = None
        if sl_value > 0:
            sl_percent = sl_value if sl_type == "SL % Movement" else abs(entry - sl_value) / entry * 100
            
            if side == "LONG":
                sl_price = actual_entry * (1 - sl_percent / 100)
            else:
                sl_price = actual_entry * (1 + sl_percent / 100)
            
            sl_price = round_price(symbol, sl_price)
            sl_price_value = sl_price

            try:
                print(f"\n🛑 Placing SL order @ {sl_price}")
                # Use STOP instead of STOP_MARKET with closePosition
                sl_order = client.futures_create_order(
                    symbol=symbol,
                    side=exit_side,
                    type="STOP",
                    stopPrice=sl_price,
                    price=sl_price,  # Required for STOP orders
                    timeInForce="GTC",
                    closePosition="true"
                )
                print(f"✅ Stop Loss placed: {sl_order['orderId']}")
            except BinanceAPIException as e:
                print(f"❌ SL Order Error: {e}")
                # Don't return error, continue with trade
                print(f"⚠️ Trade executed but SL placement failed")

        # -------- TAKE PROFIT 1 --------
        if tp1 > 0 and tp1_pct > 0:
            tp1_price = round_price(symbol, tp1)
            tp1_qty = round_qty(symbol, qty * (tp1_pct / 100))
            
            try:
                print(f"\n🎯 Placing TP1 order: {tp1_qty} @ {tp1_price}")
                # Use TAKE_PROFIT instead of TAKE_PROFIT_MARKET
                tp1_order = client.futures_create_order(
                    symbol=symbol,
                    side=exit_side,
                    type="TAKE_PROFIT",
                    stopPrice=tp1_price,
                    price=tp1_price,  # Required for TAKE_PROFIT orders
                    quantity=tp1_qty,
                    timeInForce="GTC"
                )
                print(f"✅ TP1 placed: {tp1_order['orderId']}")
            except BinanceAPIException as e:
                print(f"❌ TP1 Order Error: {e}")
        
        # -------- TAKE PROFIT 2 --------
        if tp2 > 0:
            tp2_price = round_price(symbol, tp2)
            
            try:
                print(f"\n🎯 Placing TP2 order @ {tp2_price}")
                
                if tp1 > 0 and tp1_pct > 0:
                    # Close remaining position
                    tp2_qty = round_qty(symbol, qty * ((100 - tp1_pct) / 100))
                    tp2_order = client.futures_create_order(
                        symbol=symbol,
                        side=exit_side,
                        type="TAKE_PROFIT",
                        stopPrice=tp2_price,
                        price=tp2_price,  # Required for TAKE_PROFIT orders
                        quantity=tp2_qty,
                        timeInForce="GTC"
                    )
                else:
                    # Close entire position using closePosition
                    tp2_order = client.futures_create_order(
                        symbol=symbol,
                        side=exit_side,
                        type="TAKE_PROFIT",
                        stopPrice=tp2_price,
                        price=tp2_price,
                        timeInForce="GTC",
                        closePosition="true"
                    )
                print(f"✅ TP2 placed: {tp2_order['orderId']}")
            except BinanceAPIException as e:
                print(f"❌ TP2 Order Error: {e}")

        # -------- LOG TRADE IN SESSION --------
        trade_log = {
            "time": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            "symbol": symbol,
            "side": side,
            "units": qty,
            "leverage": leverage,
            "entry": round(actual_entry, 4),
            "sl": round(sl_price_value, 4) if sl_price_value else None,
            "tp1": round(tp1, 4) if tp1 > 0 else None,
            "tp2": round(tp2, 4) if tp2 > 0 else None
        }
        
        if "trades" not in session:
            session["trades"] = []
        session["trades"].append(trade_log)
        session.modified = True
        
        # Update stats
        stats["total"] += 1
        if "stats" not in session:
            session["stats"] = {}
        session["stats"][today] = stats
        session.modified = True
        
        print(f"\n✅ Trade logged successfully! Total trades today: {stats['total']}")
        
        return {"success": True, "message": f"✅ Order placed! Entry: {actual_entry}, SL: {sl_price_value}"}

    except BinanceAPIException as e:
        error_msg = f"Binance API Error: {e.message}"
        print(f"❌ {error_msg}")
        traceback.print_exc()
        return {"success": False, "message": error_msg}
    except Exception as e:
        error_msg = f"Error: {str(e)}"
        print(f"❌ {error_msg}")
        traceback.print_exc()
        return {"success": False, "message": error_msg}
