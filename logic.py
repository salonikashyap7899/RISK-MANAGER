from flask import session
from datetime import datetime
from math import floor
from decimal import Decimal, ROUND_DOWN
from binance.client import Client
import config

client = Client(config.BINANCE_KEY, config.BINANCE_SECRET)

# =========================
# PRECISION HELPERS
# =========================

def get_symbol_precisions(symbol):
    info = client.futures_exchange_info()
    for s in info["symbols"]:
        if s["symbol"] == symbol:
            qty_p = price_p = 0
            for f in s["filters"]:
                if f["filterType"] == "LOT_SIZE":
                    qty_p = abs(Decimal(f["stepSize"]).as_tuple().exponent)
                if f["filterType"] == "PRICE_FILTER":
                    price_p = abs(Decimal(f["tickSize"]).as_tuple().exponent)
            return qty_p, price_p
    return 3, 4  # safe fallback

def rq(val, precision):
    return float(
        Decimal(val).quantize(
            Decimal(f"1e-{precision}"),
            rounding=ROUND_DOWN
        )
    )

def rp(val, precision):
    return float(
        Decimal(val).quantize(
            Decimal(f"1e-{precision}"),
            rounding=ROUND_DOWN
        )
    )

# =========================
# SESSION
# =========================

def initialize_session():
    session.setdefault("trades", [])
    session.setdefault("stats", {})

# =========================
# BALANCE
# =========================

def get_live_balance():
    try:
        acc = client.futures_account()
        usdt = next(i for i in acc["assets"] if i["asset"] == "USDT")
        available = float(usdt["availableBalance"])
        margin = float(usdt["initialMargin"])
        return available + margin, margin
    except:
        return None, None

# =========================
# POSITION SIZING (RESTORED)
# =========================

def calculate_position_sizing(unutilized_margin, entry, sl_type, sl_value):
    """
    SAME logic as your original project
    No changes made
    """
    try:
        if sl_value <= 0:
            return {"error": "SL Required"}

        risk_amount = unutilized_margin * 0.01

        if sl_type == "SL Points":
            sl_pct = (sl_value / float(entry)) * 100
        else:
            sl_pct = sl_value

        movement = sl_pct + 0.2

        notional = (risk_amount / movement) * 100
        suggested_units = notional / float(entry)
        suggested_leverage = min(100, floor(100 / movement))

        return {
            "suggested_units": suggested_units,
            "suggested_leverage": int(max(1, suggested_leverage)),
            "risk_amount": round(risk_amount, 2),
            "error": None
        }

    except:
        return {"error": "Invalid sizing calculation"}

# =========================
# EXECUTE TRADE
# =========================

def execute_trade_action(balance, symbol, side, entry, order_type,
                         sl_type, sl_value, sizing,
                         u_units, u_lev, margin_mode,
                         tp1, tp1_pct, tp2):

    today = datetime.utcnow().date().isoformat()
    stats = session["stats"].get(today, {"total": 0, "symbols": {}})

    if stats["total"] >= 4:
        return {"success": False, "message": "Daily trade limit reached"}

    try:
        # ---- One-way mode (safe) ----
        try:
            if client.futures_get_position_mode()["dualSidePosition"]:
                client.futures_change_position_mode(dualSidePosition=False)
        except:
            pass

        # ---- Leverage ----
        lev = int(u_lev if u_lev > 0 else sizing["suggested_leverage"])
        client.futures_change_leverage(symbol=symbol, leverage=lev)

        # ---- Precision ----
        qty_p, price_p = get_symbol_precisions(symbol)
        units = u_units if u_units > 0 else sizing["suggested_units"]
        qty = rq(abs(units), qty_p)

        buy = Client.SIDE_BUY
        sell = Client.SIDE_SELL
        main_side = buy if side == "LONG" else sell
        exit_side = sell if side == "LONG" else buy

        # ===== MAIN ORDER =====
        if order_type == "MARKET":
            client.futures_create_order(
                symbol=symbol,
                side=main_side,
                type="MARKET",
                quantity=qty
            )
        else:
            client.futures_create_order(
                symbol=symbol,
                side=main_side,
                type="LIMIT",
                price=rp(entry, price_p),
                quantity=qty,
                timeInForce="GTC"
            )

        # ===== STOP LOSS (LIMIT) =====
        if sl_value > 0:
            if sl_type == "SL Points":
                sl_price = entry - sl_value if side == "LONG" else entry + sl_value
            else:
                sl_price = entry * (1 - sl_value / 100) if side == "LONG" else entry * (1 + sl_value / 100)

            client.futures_create_order(
                symbol=symbol,
                side=exit_side,
                type="LIMIT",
                price=rp(sl_price, price_p),
                quantity=qty,
                timeInForce="GTC",
                reduceOnly=True
            )

        # ===== TP1 =====
        if tp1 > 0:
            tp1_qty = rq(qty * (tp1_pct / 100), qty_p)
            if tp1_qty > 0:
                client.futures_create_order(
                    symbol=symbol,
                    side=exit_side,
                    type="LIMIT",
                    price=rp(tp1, price_p),
                    quantity=tp1_qty,
                    timeInForce="GTC",
                    reduceOnly=True
                )

        # ===== TP2 =====
        if tp2 > 0:
            tp2_qty = rq(qty * ((100 - tp1_pct) / 100), qty_p)
            if tp2_qty > 0:
                client.futures_create_order(
                    symbol=symbol,
                    side=exit_side,
                    type="LIMIT",
                    price=rp(tp2, price_p),
                    quantity=tp2_qty,
                    timeInForce="GTC",
                    reduceOnly=True
                )

        stats["total"] += 1
        session["stats"][today] = stats
        session["trades"].append({
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "time": datetime.utcnow().isoformat()
        })
        session.modified = True

        return {"success": True, "message": "Trade executed successfully"}

    except Exception as e:
        return {"success": False, "message": str(e)}

# =========================
# UTIL
# =========================

def get_all_exchange_symbols():
    try:
        info = client.futures_exchange_info()
        return sorted(
            s["symbol"] for s in info["symbols"]
            if s["status"] == "TRADING" and s["quoteAsset"] == "USDT"
        )
    except:
        return ["BTCUSDT", "ETHUSDT"]

def get_live_price(symbol):
    try:
        return float(client.futures_symbol_ticker(symbol=symbol)["price"])
    except:
        return None
