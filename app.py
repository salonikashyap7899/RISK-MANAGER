from flask import Flask, render_template, request, session, jsonify, redirect, url_for
import logic

app = Flask(__name__)
app.secret_key = "risk_manager_secret"

@app.route("/get_live_price/<symbol>")
def live_price(symbol):
    return jsonify({"price": logic.get_live_price(symbol)})

@app.route("/", methods=["GET", "POST"])
def index():
    logic.initialize_session()

    symbols = logic.get_all_exchange_symbols()
    balance, used = logic.get_live_balance()
    unutilized = max(balance - used, 0)

    symbol = request.form.get("symbol", "BTCUSDT")
    side = request.form.get("side", "LONG")
    sl_type = request.form.get("sl_type", "SL % Movement")
    sl_value = float(request.form.get("sl_value") or 0)

    entry = logic.get_live_price(symbol)
    sizing = logic.calculate_position_sizing(unutilized, entry, sl_type, sl_value)

    if request.method == "POST" and "place_order" in request.form:
        status = logic.execute_trade_action(
            balance, symbol, side, entry,
            sl_type, sl_value, sizing,
            float(request.form.get("user_units") or 0),
            float(request.form.get("user_lev") or 0),
            request.form.get("margin_mode", "ISOLATED")
        )
        session["last_status"] = status
        return redirect(url_for("index"))

    trade_status = session.pop("last_status", None)

    return render_template(
        "index.html",
        symbols=symbols,
        selected_symbol=symbol,
        default_side=side,
        default_entry=entry,
        default_sl_value=sl_value,
        default_sl_type=sl_type,
        sizing=sizing,
        trades=session["trades"],
        trade_status=trade_status,
        unutilized=unutilized
    )

if __name__ == "__main__":
    app.run(debug=True)
