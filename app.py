from flask import Flask, render_template, request, session, jsonify, redirect, url_for
from flask_session import Session
from datetime import datetime
import logic
import os

app = Flask(__name__)
app.secret_key = "trading_secret_key_ultra_secure_2025"

# Configure server-side session
app.config['SESSION_TYPE'] = 'filesystem'
app.config['SESSION_PERMANENT'] = False
app.config['SESSION_USE_SIGNER'] = True
app.config['SESSION_FILE_DIR'] = '/tmp/flask_session'
app.config['SESSION_FILE_THRESHOLD'] = 500

# Initialize session
Session(app)

@app.route("/get_live_price/<symbol>")
def live_price_api(symbol):
    price = logic.get_live_price(symbol)
    return jsonify({"price": price if price else 0})

@app.route("/get_open_positions")
def get_open_positions_api():
    """NEW ENDPOINT - FIX #1: Returns live positions with P&L"""
    positions = logic.get_open_positions()
    return jsonify({"positions": positions})

@app.route("/", methods=["GET", "POST"])
def index():
    logic.initialize_session()

    symbols = logic.get_all_exchange_symbols()
    live_bal, live_margin = logic.get_live_balance()

    balance = live_bal or 0.0
    margin_used = live_margin or 0.0
    unutilized = max(balance - margin_used, 0.0)

    selected_symbol = request.form.get("symbol", "BTCUSDT")
    side = request.form.get("side", "LONG")
    order_type = request.form.get("order_type", "MARKET")
    margin_mode = request.form.get("margin_mode", "ISOLATED")

    entry = float(request.form.get("entry") or logic.get_live_price(selected_symbol) or 0)
    sl_type = request.form.get("sl_type", "SL % Movement")
    sl_val = float(request.form.get("sl_value") or 0)

    # TP Variables
    tp1 = float(request.form.get("tp1") or 0)
    tp1_pct = float(request.form.get("tp1_pct") or 0)
    tp2 = float(request.form.get("tp2") or 0)

    sizing = logic.calculate_position_sizing(unutilized, entry, sl_type, sl_val)
    trade_status = session.pop("trade_status", None)

    if request.method == "POST" and "place_order" in request.form and not sizing.get("error"):
        result = logic.execute_trade_action(
            balance,
            selected_symbol,
            side,
            entry,
            order_type,
            sl_type,
            sl_val,
            sizing,
            float(request.form.get("user_units") or 0),
            float(request.form.get("user_lev") or 0),
            margin_mode,
            tp1,
            tp1_pct,
            tp2
        )
        session["trade_status"] = result
        session.modified = True
        return redirect(url_for("index"))

    # Get trades for display
    trades = session.get("trades", [])
    
    return render_template(
        "index.html",
        trade_status=trade_status,
        sizing=sizing,
        trades=trades,
        balance=round(balance, 2),
        unutilized=round(unutilized, 2),
        symbols=symbols,
        selected_symbol=selected_symbol,
        default_entry=entry,
        default_sl_value=sl_val,
        default_sl_type=sl_type,
        default_side=side,
        order_type=order_type,
        margin_mode=margin_mode,
        tp1=tp1,
        tp1_pct=tp1_pct,
        tp2=tp2
    )

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000, debug=True)
