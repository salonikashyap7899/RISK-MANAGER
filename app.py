from flask import Flask, render_template, request, session, jsonify
from datetime import datetime
import logic
import os

app = Flask(__name__)
app.secret_key = "trading_secret_key" # Essential for session management

@app.route("/get_live_price/<symbol>")
def live_price_api(symbol):
    price = logic.get_live_price(symbol)
    return jsonify({"price": price})

@app.route("/", methods=["GET", "POST"])
def index():
    logic.initialize_session()
    all_symbols = logic.get_all_exchange_symbols()
    live_bal, live_margin = logic.get_live_balance()
    
    # Fallback values for UI if API is slow
    balance = live_bal if live_bal is not None else 1000.0
    margin_used = live_margin if live_margin is not None else 0.0
    unutilized = max(balance - margin_used, 0.01)

    selected_symbol = request.form.get("symbol", "BTCUSDT")
    prev_symbol = request.form.get("prev_symbol", "")
    order_type = request.form.get("order_type", "MARKET")
    
    # Auto-fetch entry price
    if selected_symbol != prev_symbol or not request.form.get("entry"):
        entry = logic.get_live_price(selected_symbol) or 0.0
    else:
        entry = float(request.form.get("entry", 0))

    sl_type = request.form.get("sl_type", "SL % Movement")
    sl_val = float(request.form.get("sl_value", 0))
    margin_mode = request.form.get("margin_mode", "ISOLATED")
    
    sizing = logic.calculate_position_sizing(unutilized, entry, sl_type, sl_val)
    trade_status = None

    if request.method == "POST" and 'place_order' in request.form:
        trade_status = logic.execute_trade_action(
            balance, selected_symbol, request.form.get("side", "LONG"), entry, 
            order_type, sl_type, sl_val, sizing,
            float(request.form.get("user_units") or 0), 
            float(request.form.get("user_lev") or 0),
            margin_mode, 0, 50, 0
        )

    return render_template(
        "index.html",
        trade_status=trade_status,
        sizing=sizing,
        trades=session.get("trades", [])[-10:], # Matches HTML requirements
        balance=round(balance, 2),
        unutilized=round(unutilized, 2),
        symbols=all_symbols,
        selected_symbol=selected_symbol,
        default_entry=entry,
        default_sl_value=sl_val,
        default_sl_type=sl_type,
        default_side=request.form.get("side", "LONG"),
        margin_mode=margin_mode,
        order_type=order_type,
        datetime=datetime
    )

if __name__ == "__main__":
    app.run(debug=True, port=5000)