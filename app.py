from flask import Flask, render_template, request, session, jsonify
from datetime import datetime
import logic

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

    if live_bal is None or live_margin is None:
        balance = 0.0
        margin_used = 0.0
    else:
        balance = live_bal
        margin_used = live_margin

    unutilized = max(balance - margin_used, 0.0)

    selected_symbol = request.form.get("symbol", "BTCUSDT")
    order_type = request.form.get("order_type", "MARKET")

    entry = logic.get_live_price(selected_symbol) or 0.0

    sl_type = request.form.get("sl_type", "SL % Movement")
    sl_val = float(request.form.get("sl_value", 0))
    margin_mode = request.form.get("margin_mode", "ISOLATED")

    sizing = logic.calculate_position_sizing(unutilized, entry, sl_type, sl_val)
    trade_status = None

    if request.method == "POST" and "place_order" in request.form:
        trade_status = logic.execute_trade_action(
            balance, selected_symbol, request.form.get("side", "LONG"),
            entry, order_type, sl_type, sl_val, sizing,
            float(request.form.get("user_units") or 0),
            float(request.form.get("user_lev") or 0),
            margin_mode, 0, 50, 0
        )

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
        margin_mode=margin_mode,
        order_type=order_type,
        datetime=datetime
    )

if __name__ == "__main__":
    app.run(debug=True)
