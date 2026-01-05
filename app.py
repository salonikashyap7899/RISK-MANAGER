from flask import Flask, render_template, request, session, jsonify, redirect, url_for
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
    symbols = logic.get_all_exchange_symbols()
    live_bal, live_margin = logic.get_live_balance()
    balance, unutilized = live_bal or 0.0, max((live_bal or 0.0) - (live_margin or 0.0), 0.0)

    selected_symbol = request.form.get("symbol", "BTCUSDT")
    side = request.form.get("side", "LONG")
    entry = float(request.form.get("entry") or logic.get_live_price(selected_symbol) or 0)
    sl_type = request.form.get("sl_type", "SL % Movement")
    sl_val = float(request.form.get("sl_value") or 0)

    sizing = logic.calculate_position_sizing(unutilized, entry, sl_type, sl_val)
    trade_status = session.pop("trade_status", None)

    if request.method == "POST" and "place_order" in request.form and not sizing.get("error"):
        result = logic.execute_trade_action(
            balance, selected_symbol, side, entry, "MARKET",
            sl_type, sl_val, sizing,
            request.form.get("user_units"), request.form.get("user_lev"),
            "ISOLATED", 0, 0, 0
        )
        session["trade_status"] = result
        return redirect(url_for("index"))

    return render_template(
        "index.html", trade_status=trade_status, sizing=sizing,
        trades=session.get("trades", []), unutilized=unutilized,
        symbols=symbols, selected_symbol=selected_symbol,
        default_entry=entry, default_sl_value=sl_val, default_sl_type=sl_type, default_side=side
    )

if __name__ == "__main__":
    app.run(debug=True)