# app.py
from flask import Flask, render_template, request, session
from datetime import datetime
import config 
from logic import initialize_session, calculate_position_sizing, execute_trade_action, get_live_balance
from calculations import calculate_targets_from_form, calculate_unutilized_capital

app = Flask(__name__)
app.secret_key = "replace-this-secret-key"
BROKER_SYMBOLS = ["BTCUSD", "ETHUSD", "SOLUSD", "XAUUSD", "EURUSD"]

def generate_chart_html(symbol):
    return f"""<div class="tradingview-widget-container"><div id="tradingview_chart"></div><script type="text/javascript" src="https://s3.tradingview.com/tv.js"></script><script type="text/javascript">new TradingView.widget({{"width": "100%","height": "100%","symbol": "{symbol}","interval": "1","timezone": "Etc/UTC","theme": "dark","style": "1","locale": "en","enable_publishing": false,"withdateranges": true,"allow_symbol_change": false,"container_id": "tradingview_chart"}});</script></div>"""

@app.before_request
def before_request():
    initialize_session()

@app.route("/", methods=["GET", "POST"])
def index():
    # Sync Live Balance
    live_bal, live_margin = get_live_balance()
    if live_bal: session["capital"] = live_bal
    
    balance = session.get("capital", config.TOTAL_CAPITAL_DEFAULT)
    trades = session["trades"]
    margin_used = calculate_unutilized_capital(balance, trades)
    unutilised_capital = balance - margin_used

    selected_symbol = request.form.get("symbol", "BTCUSD")
    default_entry = float(request.form.get("entry", 27050.0))
    default_sl_value = float(request.form.get("sl_value", 100.0))
    default_sl_type = request.form.get("sl_type", "SL Points")
    default_side = request.form.get("side", "LONG")

    sizing = calculate_position_sizing(unutilised_capital, default_entry, default_sl_type, default_sl_value)
    trade_status = None

    if request.method == "POST":
        if 'place_order' in request.form:
            tp_list = calculate_targets_from_form(request.form.get("tp1_price"), request.form.get("tp1_percent"), request.form.get("tp2_price"))
            trade_status = execute_trade_action(unutilised_capital, selected_symbol, default_side, default_entry, default_sl_type, default_sl_value, "MARKET", tp_list, sizing, float(request.form.get("user_units") or 0.0), float(request.form.get("user_lev") or 0.0))

    today_iso = datetime.utcnow().date().isoformat()
    stats_today = session["stats"].get(today_iso, {"total": 0, "by_symbol": {}})

    return render_template("index.html", trade_status=trade_status, sizing=sizing, stats=stats_today, trades=trades, chart_html=generate_chart_html(f"BINANCE:{selected_symbol}"), balance=balance, margin_used=margin_used, symbols=BROKER_SYMBOLS, today=datetime.utcnow(), datetime=datetime, selected_symbol=selected_symbol, default_entry=default_entry, default_sl_value=default_sl_value, default_sl_type=default_sl_type, default_side=default_side, daily_max_trades=config.DAILY_MAX_TRADES, daily_max_per_symbol=config.DAILY_MAX_PER_SYMBOL)

if __name__ == "__main__":
    app.run(debug=True)