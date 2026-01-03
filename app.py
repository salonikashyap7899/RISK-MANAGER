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

    tp1 = float(request.form.get("tp1") or 0)
    tp1_pct = float(request.form.get("tp1_pct") or 0)
    tp2 = float(request.form.get("tp2") or 0)

    sizing = logic.calculate_position_sizing(unutilized, entry, sl_type, sl_val)
    trade_status = None

    if request.method == "POST" and "place_order" in request.form and not sizing.get("error"):
        trade_status = logic.execute_trade_action(
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
        return redirect(url_for("index"))  # 🔒 refresh-safe

    return render_template(
        "index.html",
        trade_status=trade_status,
        sizing=sizing,
        trades=session.get("trades", []),
        balance=round(balance, 2),
        unutilized=round(unutilized, 2),
        symbols=symbols,
        selected_symbol=selected_symbol,
        default_entry=entry,
        default_sl_value=sl_val,
        default_sl_type=sl_type,
        default_side=side,
        margin_mode=margin_mode,
        order_type=order_type,
        tp1=tp1,
        tp1_pct=tp1_pct,
        tp2=tp2,
        datetime=datetime
    )

if __name__ == "__main__":
    app.run(debug=True)
