# logic.py
from flask import session
from datetime import datetime
from math import ceil
from binance.client import Client
import config 

# Initialize Binance Client
client = Client(config.BINANCE_KEY, config.BINANCE_SECRET)

def initialize_session():
    if "trades" not in session: session["trades"] = []
    if "stats" not in session: session["stats"] = {}

def get_all_exchange_symbols():
    """Fetches all active USDT futures symbols from the exchange."""
    try:
        info = client.futures_exchange_info()
        symbols = [s['symbol'] for s in info['symbols'] if s['status'] == 'TRADING' and s['quoteAsset'] == 'USDT']
        return sorted(symbols)
    except:
        return ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

def get_live_balance():
    """Fetches real account balance and margin from Binance Futures."""
    try:
        acc = client.futures_account()
        return float(acc.get('totalWalletBalance', 0)), float(acc.get('totalInitialMargin', 0))
    except Exception as e:
        print(f"Connection Error: {e}")
        return None, None

def get_live_price(symbol):
    """Fetches current ticker price for the symbol."""
    try:
        ticker = client.futures_symbol_ticker(symbol=symbol)
        return float(ticker['price'])
    except:
        return None

def calculate_position_sizing(unutilized_margin, entry, sl_type, sl_value):
    """Calculates sizing based on 1% of unutilized margin and includes leverage formula."""
    try:
        if sl_value <= 0: return {"error": "SL Value Required"}
        
        entry = float(entry)
        risk_amount = unutilized_margin * 0.01  # Mandatory 1% Risk
        
        if sl_type == "SL Points":
            sl_dist = sl_value + 20
        else: # % Movement
            sl_dist = (sl_value + 0.2) / 100 * entry
            
        suggested_units = risk_amount / sl_dist if sl_dist > 0 else 0
        suggested_lev = (suggested_units * entry) / unutilized_margin if unutilized_margin > 0 else 0
        
        return {
            "suggested_units": round(suggested_units, 3),
            "suggested_leverage": ceil(suggested_lev * 2) / 2,
            "risk_amount": round(risk_amount, 2),
            "max_leverage_info": f"{ceil(suggested_lev * 2) / 2:.1f}x",
            "error": None
        }
    except:
        return {"error": "Invalid Input Data"}

def execute_trade_action(balance, symbol, side, entry, order_type, sl_type, sl_value, sizing, u_units, u_lev, margin_mode, tp1, tp1_pct, tp2):
    today = datetime.utcnow().date().isoformat()
    day_stats = session["stats"].get(today, {"total": 0, "symbols": {}})
    
    # 1. LIMIT CHECK: Max 4 Daily Trades Total
    if day_stats["total"] >= 4:
        return {"success": False, "message": "REJECTED: Daily limit of 4 trades reached."}
    
    # 2. LIMIT CHECK: Max 2 Trades per Symbol Daily
    symbol_count = day_stats["symbols"].get(symbol, 0)
    if symbol_count >= 2:
        return {"success": False, "message": f"REJECTED: Limit (2/day) reached for {symbol}."}

    try:
        units = u_units if u_units > 0 else sizing["suggested_units"]
        lev = int(u_lev if u_lev > 0 else sizing["suggested_leverage"])

        # Binance Configuration
        try:
            client.futures_change_margin_type(symbol=symbol, marginType=margin_mode.upper())
        except: pass 
        client.futures_change_leverage(symbol=symbol, leverage=lev)
        
        b_side = Client.SIDE_BUY if side == "LONG" else Client.SIDE_SELL
        
        # 3. Entry Order (Market or Limit)
        if order_type == "MARKET":
            client.futures_create_order(symbol=symbol, side=b_side, type='MARKET', quantity=abs(round(units, 3)))
        else:
            client.futures_create_order(symbol=symbol, side=b_side, type='LIMIT', timeInForce='GTC', quantity=abs(round(units, 3)), price=str(entry))

        # 4. TP Scaling Logic
        tp_side = Client.SIDE_SELL if side == "LONG" else Client.SIDE_BUY
        if tp1 > 0:
            qty1 = abs(round(units * (tp1_pct / 100), 3))
            client.futures_create_order(symbol=symbol, side=tp_side, type='LIMIT', timeInForce='GTC', quantity=qty1, price=str(tp1))
        if tp2 > 0:
            qty2 = abs(round(units - (units * (tp1_pct / 100)), 3))
            client.futures_create_order(symbol=symbol, side=tp_side, type='LIMIT', timeInForce='GTC', quantity=qty2, price=str(tp2))

        # Update Session Stats
        day_stats["total"] += 1
        day_stats["symbols"][symbol] = symbol_count + 1
        session["stats"][today] = day_stats
        
        trade = {"timestamp": datetime.utcnow().isoformat(), "symbol": symbol, "side": side, "entry_price": entry, "units": units}
        session["trades"].append(trade)
        session.modified = True
        return {"success": True, "message": f"SUCCESS: {side} {symbol} Executed"}
    except Exception as e:
        return {"success": False, "message": f"EXCHANGE ERROR: {str(e)}"}