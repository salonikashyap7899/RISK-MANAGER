# app.py
from flask import Flask, render_template, request, session, jsonify, redirect, url_for, flash
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
    
    balance = live_bal if live_bal is not None else 0.0
    margin_used = live_margin if live_margin is not None else 0.0
    unutilized = max(0, balance - margin_used)

    # Use session to carry the message across the redirect
    trade_status = session.pop('last_trade_status', None)

    selected_symbol = request.form.get("symbol", "BTCUSDT")
    prev_symbol = request.form.get("prev_symbol", "")
    order_type = request.form.get("order_type", "MARKET")
    
    # Auto-fetch price on selection
    if selected_symbol != prev_symbol or not request.form.get("entry"):
        entry = get_live_price(selected_symbol) or 0.0
    else:
        entry = float(request.form.get("entry", 0))

    sl_type = request.form.get("sl_type", "SL % Movement")
    sl_val = float(request.form.get("sl_value", 0))
    side = request.form.get("side", "LONG")

    sizing = calculate_position_sizing(unutilized, entry, sl_type, sl_val)

    if request.method == "POST" and 'place_order' in request.form:
        res = execute_trade_action(unutilized, selected_symbol, side, entry, 
                                   order_type, sl_type, sl_val, sizing, 
                                   float(request.form.get("user_units") or 0), 
                                   float(request.form.get("user_lev") or 0), 
                                   request.form.get("margin_mode"), 0, 50, 0)
        
        # Save the result to session and REDIRECT to clear the form
        session['last_trade_status'] = res
        return redirect(url_for('index'))

    chart_html = f'<script type="text/javascript" src="https://s3.tradingview.com/tv.js"></script><script type="text/javascript">new TradingView.widget({{"autosize": true, "symbol": "BINANCE:{selected_symbol}", "interval": "1", "theme": "dark", "style": "1", "container_id": "tv_chart"}});</script><div id="tv_chart" style="height:100%;"></div>'

    return render_template("index.html", 
                         trade_status=trade_status, 
                         sizing=sizing, 
                         trades=session.get("trades", []), 
                         balance=balance, 
                         unutilized=unutilized, 
                         symbols=all_symbols, 
                         selected_symbol=selected_symbol, 
                         default_entry=entry, 
                         default_sl_value=sl_val, 
                         default_sl_type=sl_type, 
                         default_side=side, 
                         margin_mode=request.form.get("margin_mode", "ISOLATED"), 
                         order_type=order_type, 
                         datetime=datetime, 
                         chart_html=chart_html,
                         tp1=0, tp1_pct=50, tp2=0)

if __name__ == "__main__":
    app.run(debug=True)