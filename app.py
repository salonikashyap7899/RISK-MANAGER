from flask import Flask, render_template, request, session
from datetime import datetime

from logic import (
    initialize_session,
    calculate_position_sizing,
    execute_trade_action,
)
from calculations import calculate_targets_from_form


app = Flask(__name__)
app.secret_key = "replace-this-secret-key"


# --------------------------
# TradingView Chart Generator
# --------------------------
def generate_chart_html(symbol):
    return f"""
    <!-- TradingView BEGIN -->
    <div class="tradingview-widget-container">
      <div id="tradingview_chart"></div>

      <script type="text/javascript" src="https://s3.tradingview.com/tv.js"></script>

      <script type="text/javascript">
        new TradingView.widget({{
          "width": "100%",
          "height": 500,
          "symbol": "{symbol}",
          "interval": "1",
          "timezone": "Etc/UTC",
          "theme": "dark",
          "style": "1",
          "locale": "en",
          "enable_publishing": false,
          "withdateranges": true,
          "allow_symbol_change": false,
          "container_id": "tradingview_chart"
        }});
      </script>

    </div>
    <!-- TradingView END -->
    """


@app.before_request
def before_request():
    initialize_session()


@app.route("/", methods=["GET", "POST"])
def index():

    trades = session["trades"]
    stats = session["stats"]

    today = datetime.utcnow().date().isoformat()
    stats_today = stats.get(today, {"total": 0, "by_symbol": {}})

    trade_status = None
    sizing = None

    # --------------------------
    # GET SELECTED SYMBOL
    # --------------------------
    selected_symbol = request.form.get("symbol", "BTCUSD")
    tv_symbol = f"BINANCE:{selected_symbol}"  # TradingView format

    # Build TradingView chart HTML
    chart_html = generate_chart_html(tv_symbol)

    # --------------------------
    # Handle Order Form
    # --------------------------
    if request.method == "POST" and "entry" in request.form:

        form = request.form

        # Parse inputs
        symbol = form.get("symbol")
        side = form.get("side")
        order_type = form.get("order_type") or "market"
        entry = float(form.get("entry") or 0.0)

        sl_type = form.get("sl_type")
        sl_value = float(form.get("sl_value") or 0.0)

        user_units = float(form.get("user_units") or 0.0)
        user_lev = float(form.get("user_lev") or 0.0)

        tp1_price = float(form.get("tp1_price") or 0.0)
        tp1_percent = int(form.get("tp1_percent") or 0)
        tp2_price = float(form.get("tp2_price") or 0.0)

        tp_list = calculate_targets_from_form(tp1_price, tp1_percent, tp2_price)

        # Sizing preview
        sizing = calculate_position_sizing(
            session.get("capital", 10000.0),
            entry,
            sl_type,
            sl_value
        )

        # Execute trade simulation
        resp = execute_trade_action(
            balance=session.get("capital", 10000.0),
            symbol=symbol,
            side=side,
            entry=entry,
            sl=sl_value,
            suggested_units=sizing.get("suggested_position") or sizing.get("suggested_lot"),
            suggested_lev=sizing.get("suggested_leverage"),
            user_units=user_units,
            user_lev=user_lev,
            sl_type=sl_type,
            sl_value=sl_value,
            order_type=order_type,
            tp_list=tp_list,
        )

        trade_status = resp

    # --------------------------
    # RENDER TEMPLATE
    # --------------------------
    return render_template(
        "index.html",
        trade_status=trade_status,
        sizing=sizing,
        stats=stats_today,
        trades=session["trades"],
        chart_html=chart_html,
    )


@app.route("/log")
def trade_log():
    trades = session["trades"]
    today = datetime.utcnow().date().isoformat()
    today_trades = [t for t in trades if t["date"] == today]
    return render_template("trade_log.html", trades=today_trades)


if __name__ == "__main__":
    app.run(debug=True)
