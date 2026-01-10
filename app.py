from flask import Flask, render_template, request, session, jsonify, redirect, url_for
import logic
import config

app = Flask(__name__)
app.secret_key = "trading_secret_key_ultra_secure_2025"

@app.route("/", methods=["GET", "POST"])
def index():
    # Setup basics
    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]
    selected_symbol = request.form.get("symbol") or request.args.get("symbol") or "BTCUSDT"
    
    # Get balance and price
    client = logic.get_client()
    try:
        balance_info = client.futures_account_balance()
        balance = next(float(b['balance']) for b in balance_info if b['asset'] == 'USDT')
    except:
        balance = 100.0

    entry = float(request.form.get("entry") or logic.get_live_price(selected_symbol))
    sl_val = float(request.form.get("sl_val") or 1.0)
    sl_type = request.form.get("sl_type") or "PERCENT"
    side = request.form.get("side") or "LONG"
    order_type = request.form.get("order_type") or "MARKET"
    margin_mode = request.form.get("margin_mode") or "ISOLATED"

    # Risk pool calculation
    unutilized = balance * (config.MAX_RISK_PERCENT / 100)
    sizing = logic.calculate_position_sizing(balance, entry, sl_type, sl_val)
    
    trade_status = session.pop("trade_status", None)

    if request.method == "POST" and "place_order" in request.form:
        result = logic.execute_trade_action(
            balance, selected_symbol, side, entry, order_type, 
            sl_type, sl_val, sizing, 
            float(request.form.get("user_units") or 0),
            float(request.form.get("user_lev") or 0),
            margin_mode
        )
        session["trade_status"] = result
        return redirect(url_for("index", symbol=selected_symbol))

    return render_template(
        "index.html",
        trade_status=trade_status,
        sizing=sizing,
        balance=balance,
        unutilized=unutilized,
        symbols=symbols,
        selected_symbol=selected_symbol,
        default_entry=entry,
        default_sl_value=sl_val,
        default_sl_type=sl_type,
        today_stats=logic.get_today_stats()
    )

@app.route("/get_live_price/<symbol>")
def live_price_api(symbol):
    return jsonify({"price": logic.get_live_price(symbol)})

@app.route("/get_open_positions")
def get_open_positions_api():
    return jsonify({"positions": logic.get_open_positions()})

if __name__ == "__main__":
    app.run(debug=True, port=5000)