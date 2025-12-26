# app.py
from flask import Flask, render_template, request, session, jsonify, redirect, url_for
from datetime import datetime
import config 
from logic import (initialize_session, calculate_position_sizing, execute_trade_action, 
                   get_live_balance, get_live_price, get_all_exchange_symbols)

app = Flask(__name__)
app.secret_key = "trading_secret_key"

@app.route("/get_live_price/<symbol>")
def live_price_api(symbol):
    price = get_live_price(symbol)
    return jsonify({"price": price})

@app.route("/", methods=["GET", "POST"])
def index():
    initialize_session()
    all_symbols = get_all_exchange_symbols()
    live_bal, live_margin = get_live_balance()
    
    # FIXED: Changed default from 1000.0 to 0.0
    balance = live_bal if live_bal is not None else 0.0
    margin_used = live_margin if live_margin is not None else 0.0
    unutilized = balance - margin_used

    # Retrieve trade status from session (Fixes display after redirect)
    trade_status = session.pop('last_trade_status', None)

    selected_symbol = request.form.get("symbol", "BTCUSDT")
    prev_symbol = request.form.get("prev_symbol", "")
    order_type = request.form.get("order_type", "MARKET")
    
    if selected_symbol != prev_symbol or not request.form.get("entry"):
        entry = get_live_price(selected_symbol) or 0.0
    else:
        entry = float(request.form.get("entry", 0))

    sl_type = request.form.get("sl_type", "SL Points")
    sl_val = float(request.form.get("sl_value", 0))
    margin_mode = request.form.get("margin_mode", "ISOLATED")
    tp1 = float(request.form.get("tp1") or 0)
    tp1_pct = float(request.form.get("tp1_pct") or 50)
    tp2 = float(request.form.get("tp2") or 0)

    sizing = calculate_position_sizing(unutilized, entry, sl_type, sl_val)

    if request.method == "POST" and 'place_order' in request.form:
        if sl_val <= 0:
            session['last_trade_status'] = {"success": False, "message": "ERROR: SL is mandatory."}
        else:
            # Added redirect to fix the refresh duplication issue
            res = execute_trade_action(unutilized, selected_symbol, request.form.get("side"), entry, order_type, sl_type, sl_val, sizing, float(request.form.get("user_units") or 0), float(request.form.get("user_lev") or 0), margin_mode, tp1, tp1_pct, tp2)
            session['last_trade_status'] = res
        return redirect(url_for('index'))

    chart_html = f'<script type="text/javascript" src="https://s3.tradingview.com/tv.js"></script><script type="text/javascript">new TradingView.widget({{"autosize": true, "symbol": "BINANCE:{selected_symbol}", "interval": "1", "theme": "dark", "style": "1", "container_id": "tv_chart"}});</script><div id="tv_chart" style="height:100%;"></div>'

    return render_template("index.html", trade_status=trade_status, sizing=sizing, trades=session["trades"], balance=balance, margin_used=margin_used, unutilized=unutilized, symbols=all_symbols, selected_symbol=selected_symbol, default_entry=entry, default_sl_value=sl_val, default_sl_type=sl_type, default_side=request.form.get("side", "LONG"), margin_mode=margin_mode, tp1=tp1, tp1_pct=tp1_pct, tp2=tp2, order_type=order_type, datetime=datetime, chart_html=chart_html)

if __name__ == "__main__":
    app.run(debug=True)