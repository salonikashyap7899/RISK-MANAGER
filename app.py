from flask import Flask, render_template, request, jsonify
from datetime import datetime

app = Flask(__name__)

# -------------------------------------------------
# BASIC HELPERS (KEEP SIMPLE – NO FEATURE REMOVED)
# -------------------------------------------------

def get_all_exchange_symbols():
    return [
        "BTCUSDT", "ETHUSDT", "BNBUSDT",
        "XRPUSDT", "SOLUSDT", "XLMUSDT"
    ]

def get_live_price(symbol):
    # Lightweight fallback price (chart is real-time anyway)
    prices = {
        "BTCUSDT": 43000,
        "ETHUSDT": 2300,
        "BNBUSDT": 310,
        "XRPUSDT": 0.62,
        "SOLUSDT": 98,
        "XLMUSDT": 0.224
    }
    return prices.get(symbol, 1)

def get_live_balance():
    return 15.0

def calculate_position_sizing(entry, sl_value):
    if entry <= 0 or sl_value <= 0:
        return {
            "error": True,
            "risk_amount": 0,
            "suggested_units": 0,
            "suggested_leverage": 0
        }

    risk_amount = round(get_live_balance() * 0.01, 2)
    sl_percent = (sl_value / entry) * 100
    leverage = round(100 / (sl_percent + 0.2), 2)
    units = round((risk_amount / (sl_percent + 0.2)) * 100, 4)

    return {
        "error": False,
        "risk_amount": risk_amount,
        "suggested_units": units,
        "suggested_leverage": leverage
    }

# -------------------------------------------------
# 🔥 REAL LIVE TRADINGVIEW CHART (BINANCE)
# -------------------------------------------------

def generate_tradingview_chart(symbol):
    return f"""
    <div class="tradingview-widget-container">
      <div id="tradingview_chart"></div>
      <script type="text/javascript" src="https://s3.tradingview.com/tv.js"></script>
      <script type="text/javascript">
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
        "hide_top_toolbar": false,
        "save_image": false,
        "container_id": "tradingview_chart"
      }});
      </script>
    </div>
    """

# -------------------------------------------------
# MAIN ROUTE
# -------------------------------------------------

@app.route("/", methods=["GET", "POST"])
def index():
    symbols = get_all_exchange_symbols()

    selected_symbol = request.form.get("symbol", "BTCUSDT")
    default_side = request.form.get("side", "LONG")
    order_type = request.form.get("order_type", "MARKET")
    margin_mode = request.form.get("margin_mode", "ISOLATED")

    entry = float(request.form.get("entry", 0) or 0)
    sl_value = float(request.form.get("sl_value", 0) or 0)

    tp1 = float(request.form.get("tp1", 0) or 0)
    tp2 = float(request.form.get("tp2", 0) or 0)
    tp1_pct = float(request.form.get("tp1_pct", 0) or 0)

    # ✅ CHART IS GENERATED ALWAYS (IMPORTANT)
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
            trade_status = {"success": True, "message": "Order validated (execution disabled)"}

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

# -------------------------------------------------
# LIVE PRICE ENDPOINT (USED BY ENTRY AUTO-FILL)
# -------------------------------------------------

@app.route("/get_live_price/<symbol>")
def live_price(symbol):
    return jsonify({"price": get_live_price(symbol)})

# -------------------------------------------------
# RUN
# -------------------------------------------------

if __name__ == "__main__":
    app.run(debug=True)
