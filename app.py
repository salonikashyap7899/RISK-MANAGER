# app.py
import os
from flask import Flask, render_template, request, session
from datetime import datetime
import config 
from logic import initialize_session, calculate_position_sizing, execute_trade_action
from calculations import calculate_unutilized_capital, calculate_targets_from_form

app = Flask(__name__)
app.secret_key = "proprietary_trading_key"

@app.before_request
def before_request():
    initialize_session()

@app.route("/", methods=["GET", "POST"])
def index():
    balance = session.get("capital", config.TOTAL_CAPITAL_DEFAULT)
    margin_used = calculate_unutilized_capital(balance, session["trades"])
    
    # Defaults
    selected_symbol = request.form.get("symbol", "BTCUSD")
    entry = float(request.form.get("entry", 27050.0))
    sl_type = request.form.get("sl_type", "SL Points")
    sl_val = float(request.form.get("sl_value", 100.0))
    side = request.form.get("side", "LONG")

    sizing = calculate_position_sizing(balance - margin_used, entry, sl_type, sl_val)
    trade_status = None

    if request.method == "POST" and 'place_order' in request.form:
        tp_list = calculate_targets_from_form(request.form.get("tp1_price"), request.form.get("tp1_percent"), request.form.get("tp2_price"))
        trade_status = execute_trade_action(balance - margin_used, selected_symbol, side, entry, sl_type, sl_val, "MARKET", tp_list, sizing, float(request.form.get("user_units") or 0), float(request.form.get("user_lev") or 0))

    chart_html = f'<script type="text/javascript" src="https://s3.tradingview.com/tv.js"></script><script type="text/javascript">new TradingView.widget({{"autosize":true,"symbol":"BINANCE:{selected_symbol.replace("USD","USDT")}","interval":"1","theme":"dark","style":"1","container_id":"tv_chart"}});</script><div id="tv_chart" style="height:100%;"></div>'

    return render_template("index.html", trade_status=trade_status, sizing=sizing, trades=session["trades"], balance=balance, margin_used=margin_used, symbols=["BTCUSD", "ETHUSD", "SOLUSD", "XAUUSD", "EURUSD"], selected_symbol=selected_symbol, default_entry=entry, default_sl_value=sl_val, default_sl_type=sl_type, default_side=side, datetime=datetime, stats=session.get("stats", {}).get(datetime.utcnow().date().isoformat(), {"total":0, "by_symbol":{}}), chart_html=chart_html, daily_max_trades=config.DAILY_MAX_TRADES, daily_max_per_symbol=config.DAILY_MAX_PER_SYMBOL)

if __name__ == "__main__":
    # REQUIRED FOR RAILWAY/LIVE LINK
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)