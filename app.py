from flask import Flask, render_template, request, session, jsonify, redirect, url_for
from datetime import datetime
import logic

# --- FIXED: Define app before using decorators ---
app = Flask(__name__)
app.secret_key = "trading_secret_key"

@app.route("/get_live_price/<symbol>")
def live_price_api(symbol):
    return jsonify({"price": logic.get_live_price(symbol)})

@app.route("/", methods=["GET", "POST"])
def index():
    logic.initialize_session()
    all_symbols = logic.get_all_exchange_symbols()
    live_bal, live_margin = logic.get_live_balance()
    
    balance = live_bal if live_bal is not None else 0.0
    margin_used = live_margin if live_margin is not None else 0.0
    unutilized = max(balance - margin_used, 0.0)

    selected_symbol = request.form.get("symbol", "BTCUSDT")
    entry = logic.get_live_price(selected_symbol) or 0.0
    sl_type = request.form.get("sl_type", "SL % Movement")
    sl_val = float(request.form.get("sl_value", 0))
    
    sizing = logic.calculate_position_sizing(unutilized, entry, sl_type, sl_val)

    if request.method == "POST" and "place_order" in request.form:
        # Capture form data for the trade
        status = logic.execute_trade_action(
            balance, selected_symbol, request.form.get("side", "LONG"),
            entry, request.form.get("order_type", "MARKET"), 
            sl_type, sl_val, sizing,
            float(request.form.get("user_units") or 0),
            float(request.form.get("user_lev") or 0),
            request.form.get("margin_mode", "ISOLATED")
        )
        # Store status in session to survive redirect
        session['last_status'] = status
        return redirect(url_for('index'))

    # Retrieve status for display
    trade_status = session.pop('last_status', None)

    return render_template(
        "index.html",
        trade_status=trade_status,
        sizing=sizing,
        trades=session.get("trades", []),
        balance=round(balance, 2),
        unutilized=round(unutilized, 2),
        symbols=all_symbols,
        selected_symbol=selected_symbol,
        default_entry=entry,
        default_sl_value=sl_val,
        default_sl_type=sl_type,
        default_side=request.form.get("side", "LONG"),
        margin_mode=request.form.get("margin_mode", "ISOLATED"),
        order_type=request.form.get("order_type", "MARKET")
    )

if __name__ == "__main__":
    app.run(debug=True)