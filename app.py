# app.py
from flask import Flask, render_template, request, session
from datetime import datetime
import config
from logic import initialize_session, calculate_position_sizing, execute_trade_action, get_binance_data
from calculations import calculate_targets_from_form

app = Flask(__name__)
app.secret_key = "secure_key_for_trading"

BROKER_SYMBOLS = ["BTCUSD", "ETHUSD", "SOLUSD", "BNBUSD"]

def generate_chart_html(symbol):
    return f"""<div class="tradingview-widget-container"><div id="tradingview_chart"></div>
    <script type="text/javascript" src="https://s3.tradingview.com/tv.js"></script>
    <script type="text/javascript">new TradingView.widget({{"width": "100%", "height": "100%", "symbol": "BINANCE:{symbol}T", "interval": "D", "timezone": "Etc/UTC", "theme": "dark", "style": "1", "locale": "en", "enable_publishing": false, "allow_symbol_change": true, "container_id": "tradingview_chart"}});</script></div>"""

@app.route("/", methods=["GET", "POST"])
def index():
    initialize_session()
    balance, margin_used = get_binance_data()
    
    selected_symbol = request.form.get("symbol", "BTCUSD")
    default_side = request.form.get("side", "LONG")
    entry = request.form.get("entry", 50000.0)
    sl_type = request.form.get("sl_type", "SL Points")
    sl_value = request.form.get("sl_value", 100.0)

    sizing = calculate_position_sizing(balance, entry, sl_type, sl_value)
    
    trade_status = None
    if request.method == "POST" and 'place_order' in request.form:
        tp_list = calculate_targets_from_form(request.form.get("tp1_price"), request.form.get("tp1_pct"), request.form.get("tp2_price"))
        trade_status = execute_trade_action(selected_symbol, default_side, float(entry), sl_type, float(sl_value), tp_list, sizing, float(request.form.get("user_units") or 0), float(request.form.get("user_lev") or 0))

    stats_today = session["stats"].get(datetime.utcnow().date().isoformat(), {"total": 0, "by_symbol": {}})

    return render_template("index.html", trade_status=trade_status, sizing=sizing, stats=stats_today, trades=session["trades"], chart_html=generate_chart_html(selected_symbol.replace("USD","")), balance=balance, margin_used=margin_used, symbols=BROKER_SYMBOLS, selected_symbol=selected_symbol, default_entry=entry, default_sl_value=sl_value, default_sl_type=sl_type, default_side=default_side, daily_max_trades=config.DAILY_MAX_TRADES, daily_max_per_symbol=config.DAILY_MAX_PER_SYMBOL, datetime=datetime, today=datetime.utcnow())

if __name__ == "__main__":
    app.run(debug=True)