# app.py
from flask import Flask, render_template, request, session, redirect, url_for
from datetime import datetime
import config 
from logic import (initialize_session, calculate_position_sizing, execute_trade_action, 
                   get_live_balance, get_live_price, get_all_exchange_symbols)

app = Flask(__name__)
app.secret_key = "trading_secret_key"

@app.route("/", methods=["GET", "POST"])
def index():
    initialize_session()
    all_symbols = get_all_exchange_symbols()
    live_bal, live_margin = get_live_balance()
    unutilized = max(0, (live_bal or 0) - (live_margin or 0))

    # Survival of message after redirect
    trade_status = session.pop('trade_status', None)

    selected_symbol = request.form.get("symbol", "BTCUSDT")
    prev_symbol = request.form.get("prev_symbol", "")
    order_type = request.form.get("order_type", "MARKET")
    
    # Logic to maintain the entry price correctly across refreshes/symbol changes
    if selected_symbol != prev_symbol or not request.form.get("entry"):
        entry = get_live_price(selected_symbol) or 0.0
    else:
        entry = float(request.form.get("entry", 0))

    sl_type = request.form.get("sl_type", "SL % Movement")
    sl_val = float(request.form.get("sl_value", 0))
    side = request.form.get("side", "LONG")
    margin_mode = request.form.get("margin_mode", "ISOLATED")

    sizing = calculate_position_sizing(unutilized, entry, sl_type, sl_val)

    if request.method == "POST" and 'place_order' in request.form:
        status = execute_trade_action(
            unutilized, selected_symbol, side, entry, order_type, sl_type, sl_val, sizing, 
            float(request.form.get("user_units") or 0), float(request.form.get("user_lev") or 0), 
            margin_mode, float(request.form.get("tp1") or 0), int(request.form.get("tp1_pct") or 50), float(request.form.get("tp2") or 0)
        )
        session['trade_status'] = status
        return redirect(url_for('index')) # PREVENT DUPLICATE ORDER ON REFRESH

    chart_html = f'<script src="https://s3.tradingview.com/tv.js"></script><script>new TradingView.widget({{"autosize": true, "symbol": "BINANCE:{selected_symbol}", "interval": "1", "theme": "dark", "container_id": "tv_chart"}});</script><div id="tv_chart" style="height:350px;"></div>'

    return render_template("index.html", trade_status=trade_status, sizing=sizing, trades=session.get("trades", []), 
                         balance=live_bal or 0, unutilized=unutilized, symbols=all_symbols, selected_symbol=selected_symbol, 
                         default_entry=entry, default_sl_value=sl_val, default_sl_type=sl_type, default_side=side, 
                         margin_mode=margin_mode, order_type=order_type, datetime=datetime, chart_html=chart_html,
                         tp1=float(request.form.get("tp1") or 0), tp1_pct=int(request.form.get("tp1_pct") or 50), tp2=float(request.form.get("tp2") or 0))

if __name__ == "__main__":
    app.run(debug=True)