from flask import Flask, render_template, request, jsonify
from datetime import datetime
import requests

app = Flask(__name__)

BINANCE_BASE = "https://api.binance.com"

# --------------------------------------------------
# 🔥 FETCH ALL BINANCE USDT SYMBOLS (LIVE)
# --------------------------------------------------

def get_all_exchange_symbols():
    try:
        url = f"{BINANCE_BASE}/api/v3/exchangeInfo"
        data = requests.get(url, timeout=5).json()

        symbols = [
            s["symbol"]
            for s in data["symbols"]
            if s["quoteAsset"] == "USDT" and s["status"] == "TRADING"
        ]
        return sorted(symbols)
    except Exception:
        return ["BTCUSDT"]

# --------------------------------------------------
# 🔥 LIVE PRICE (REAL BINANCE DATA)
# --------------------------------------------------

def get_live_price(symbol):
    try:
        url = f"{BINANCE_BASE}/api/v3/ticker/price?symbol={symbol}"
        return float(requests.get(url, timeout=5).json()["price"])
    except Exception:
        return 0.0

# --------------------------------------------------
# BASIC ACCOUNT HELPERS
# --------------------------------------------------

def get_live_balance():
    # Your real balance logic will go here later
    return 15.0

def calculate_position_sizing(entry, sl_value):
    if entry <= 0 or sl_value <= 0:
        return {
            "error": True,
            "risk_amount": 0,
            "suggested_units": 0,
            "suggested_leverage": 0
        }

    risk = round(get_live_balance() * 0.01, 2)
    sl_percent = (sl_value / entry) * 100
    leverage = round(100 / (sl_percent + 0.2), 2)
    units = round((risk / (sl_percent + 0.2)) * 100, 4)

    return {
        "error": False,
        "risk_amount": risk,
        "suggested_units": units,
        "suggested_leverage": leverage
    }

# --------------------------------------------------
# 🔥 REAL TRADINGVIEW LIVE CHART
# --------------------------------------------------

def generate_tradingview_chart(symbol):
    return f"""
    <div class="tradingview-widget-container">
      <div id="tradingview_chart"></div>
      <script src="https://s3.tradingview.com/tv.js"></script>
      <script>
        new TradingView.widget({{
          "width": "100%",
          "height": 380,
          "symbol": "BINANCE:{symbol}",
          "interval": "1",
          "timezone": "Asia/Kolkata",
          "theme": "dark",
          "style": "1",
          "locale": "en",
          "toolbar_bg": "#020c14",
          "enable_publishing": false,
          "save_image": false,
          "container_id": "tradingview_chart"
        }});
      </script>
    </div>
    """

# --------------------------------------------------
# MAIN ROUTE
# --------------------------------------------------

@app.route("/", methods=["GET", "POST"])
def index():
    symbols = get_all_exchange_symbols()

    selected_symbol = request.form.get("symbol", symbols[0])
    default_side = request.form.get("side", "LONG")
    order_type = request.form.get("order_type", "MARKET")
    margin_mode = request.form.get("margin_mode", "ISOLATED")

    entry = float(request.form.get("entry", 0) or 0)
    sl_value = float(request.form.get("sl_value", 0) or 0)

    tp1 = float(request.form.get("tp1", 0) or 0)
    tp2 = float(request.form.get("tp2", 0) or 0)
    tp1_pct = float(request.form.get("tp1_pct", 0) or 0)

    chart_html = generate_tradingview_chart(selected_symbol)
    unutilized = round(get_live_balance(), 2)
    sizing = calculate_position_sizing(entry, sl_value)

    trade_status = None

    if request.method == "POST" and "place_order" in request.form:
        if entry <= 0:
            trade_status = {"success": False, "message": "Invalid entry price"}
        elif sl_value <= 0:
            trade_status = {"success": False, "message": "SL value required"}
        else:
            trade_status = {"success": True, "message": "Order validated"}

    return render_template(
        "index.html",
        symbols=symbols,
        selected_symbol=selected_symbol,
        default_side=default_side,
        order_type=order_type,
        margin_mode=margin_mode,
        default_entry=entry,
        default_sl_value=sl_value,
        tp1=tp1,
        tp2=tp2,
        tp1_pct=tp1_pct,
        sizing=sizing,
        unutilized=unutilized,
        chart_html=chart_html,
        trade_status=trade_status,
        trades=[],
        datetime=datetime
    )

# --------------------------------------------------
# LIVE PRICE ENDPOINT
# --------------------------------------------------

@app.route("/get_live_price/<symbol>")
def live_price(symbol):
    return jsonify({"price": get_live_price(symbol)})

# --------------------------------------------------
# RUN
# --------------------------------------------------

if __name__ == "__main__":
    app.run(debug=True)
