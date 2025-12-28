# app.py
from flask import Flask, render_template, request, session, jsonify, redirect, url_for
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
    
    # Use real balance or 0.0 if not connected
    balance = live_bal if live_bal is not None else 0.0
    margin_used = live_margin if live_margin is not None else 0.0
    unutilized = max(0, balance - margin_used)

    # Get form values, fallback to session (for after redirect) or defaults
    selected_symbol = request.form.get("symbol") or session.get("last_symbol", "BTCUSDT")
    prev_symbol = request.form.get("prev_symbol", "")
    order_type = request.form.get("order_type") or session.get("last_order_type", "MARKET")
    
    if selected_symbol != prev_symbol or not request.form.get("entry"):
        entry = get_live_price(selected_symbol) or 0.0
    else:
        entry = float(request.form.get("entry", 0))

    sl_type = request.form.get("sl_type") or session.get("last_sl_type", "SL % Movement")
    sl_val = float(request.form.get("sl_value") or session.get("last_sl_value", 0))
    side = request.form.get("side") or session.get("last_side", "LONG")
    margin_mode = request.form.get("margin_mode") or session.get("last_margin_mode", "ISOLATED")

    sizing = calculate_position_sizing(unutilized, entry, sl_type, sl_val)
    trade_status = None

    # Handle POST request for placing order - redirect after to prevent duplicate on refresh
    if request.method == "POST" and 'place_order' in request.form:
        tp1 = float(request.form.get("tp1") or 0)
        tp1_pct = int(request.form.get("tp1_pct") or 50)
        tp2 = float(request.form.get("tp2") or 0)
        trade_status = execute_trade_action(unutilized, selected_symbol, side, entry, order_type, sl_type, sl_val, sizing, float(request.form.get("user_units") or 0), float(request.form.get("user_lev") or 0), margin_mode, tp1, tp1_pct, tp2)
        # Store trade status and form values in session, then redirect to prevent duplicate orders on refresh
        session['trade_status'] = trade_status
        session['last_symbol'] = selected_symbol
        session['last_order_type'] = order_type
        session['last_sl_type'] = sl_type
        session['last_sl_value'] = sl_val
        session['last_side'] = side
        session['last_margin_mode'] = margin_mode
        session['last_tp1'] = tp1
        session['last_tp1_pct'] = tp1_pct
        session['last_tp2'] = tp2
        return redirect(url_for('index'))
    
    # Get trade status from session if available (after redirect)
    if 'trade_status' in session:
        trade_status = session.pop('trade_status', None)

    chart_html = f'<script type="text/javascript" src="https://s3.tradingview.com/tv.js"></script><script type="text/javascript">new TradingView.widget({{"autosize": true, "symbol": "BINANCE:{selected_symbol}", "interval": "1", "theme": "dark", "style": "1", "container_id": "tv_chart"}});</script><div id="tv_chart" style="height:100%;"></div>'

    # Fixed Variables to prevent UndefinedError
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
                         margin_mode=margin_mode, 
                         order_type=order_type, 
                         datetime=datetime, 
                         chart_html=chart_html,
                         tp1=float(request.form.get("tp1") or session.get("last_tp1", 0)),
                         tp1_pct=int(request.form.get("tp1_pct") or session.get("last_tp1_pct", 50)),
                         tp2=float(request.form.get("tp2") or session.get("last_tp2", 0)))

if __name__ == "__main__":
    app.run(debug=True)