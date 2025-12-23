# app.py
from flask import Flask, render_template, request, session
from datetime import datetime
import config 
import os
from logic import (initialize_session, calculate_position_sizing, execute_trade_action, 
                   get_live_balance, get_live_price, get_all_exchange_symbols)

app = Flask(__name__)
app.secret_key = "trading_secret_key"

@app.route("/", methods=["GET", "POST"])
def index():
    initialize_session()
    
    # 1. Fetch Dynamic Symbol List
    all_symbols = get_all_exchange_symbols()
    
    live_bal, live_margin = get_live_balance()
    balance = live_bal if live_bal is not None else session.get("capital", 1000)
    margin_used = live_margin if live_margin is not None else 0
    
    selected_symbol = request.form.get("symbol", "BTCUSDT")
    prev_symbol = request.form.get("prev_symbol", "")
    order_type = request.form.get("order_type", "MARKET")
    
    # 2. Auto-fetch Price if Symbol Changed
    if selected_symbol != prev_symbol or not request.form.get("entry"):
        entry = get_live_price(selected_symbol) or 0.0
    else:
        entry = float(request.form.get("entry", 0))

    sl_type = request.form.get("sl_type", "SL Points")
    sl_val = float(request.form.get("sl_value", 0))
    margin_mode = request.form.get("margin_mode", "ISOLATED")
    
    tp1 = float(request.form.get("tp1") or 0)
    tp1_pct = float(request.form.get("tp1_pct") or 50) # Default 50%
    tp2 = float(request.form.get("tp2") or 0)

    sizing = calculate_position_sizing(balance - margin_used, entry, sl_type, sl_val)
    trade_status = None

    if request.method == "POST" and 'place_order' in request.form:
        trade_status = execute_trade_action(
            balance - margin_used, selected_symbol, request.form.get("side"), 
            entry, order_type, sl_type, sl_val, sizing, 
            float(request.form.get("user_units") or 0), 
            float(request.form.get("user_lev") or 0),
            margin_mode, tp1, tp1_pct, tp2
        )

    chart_html = f'<script type="text/javascript" src="https://s3.tradingview.com/tv.js"></script><script type="text/javascript">new TradingView.widget({{"autosize": true, "symbol": "BINANCE:{selected_symbol}", "interval": "1", "theme": "dark", "style": "1", "container_id": "tv_chart"}});</script><div id="tv_chart" style="height:100%;"></div>'

    return render_template("index.html", trade_status=trade_status, sizing=sizing, trades=session["trades"], 
                           balance=balance, margin_used=margin_used, symbols=all_symbols, 
                           selected_symbol=selected_symbol, default_entry=entry, 
                           default_sl_value=sl_val, default_sl_type=sl_type, 
                           default_side=request.form.get("side", "LONG"), 
                           margin_mode=margin_mode, tp1=tp1, tp1_pct=tp1_pct, tp2=tp2, order_type=order_type,
                           datetime=datetime, chart_html=chart_html)

if __name__ == "__main__":
    app.run(debug=True)