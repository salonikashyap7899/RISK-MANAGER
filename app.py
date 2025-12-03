from flask import Flask, render_template, request, session, redirect, url_for
from datetime import datetime

from logic import (
    initialize_session,
    calculate_position_sizing,
    execute_trade_action,
    DAILY_MAX_TRADES,
    DAILY_MAX_PER_SYMBOL,
    TOTAL_CAPITAL_DEFAULT,
)
from calculations import calculate_targets_from_form, calculate_unutilized_capital


# List of symbols from PyQt app
BROKER_SYMBOLS = ["BTCUSD", "ETHUSD", "SOLUSD", "XAUUSD", "EURUSD"]


app = Flask(__name__)
app.secret_key = "replace-this-secret-key"


# --------------------------
# TradingView Chart Generator
# --------------------------
def generate_chart_html(symbol):
    return f"""
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
    """


@app.before_request
def before_request():
    initialize_session()


@app.route("/", methods=["GET", "POST"])
def index():

    trades = session["trades"]
    stats = session["stats"]
    balance = session.get("capital", TOTAL_CAPITAL_DEFAULT)

    today = datetime.utcnow().date().isoformat()
    stats_today = stats.get(today, {"total": 0, "by_symbol": {}})

    trade_status = None
    sizing = None
    
    # --- Default/Current values ---
    selected_symbol = request.form.get("symbol", "BTCUSD")
    default_entry = request.form.get("entry", 27050.0)
    default_sl_value = request.form.get("sl_value", 100.0)
    default_sl_type = request.form.get("sl_type", "SL Points")
    default_side = request.form.get("side", "LONG")
    default_order_type = "MARKET"

    # Margin Used calculation
    margin_used = calculate_unutilized_capital(balance, trades)

    # --------------------------
    # Handle Order Form POST and Sizing Preview
    # --------------------------
    if request.method == "POST":

        form = request.form

        # Handle capital input update 
        try:
            capital_input = float(form.get("capital_input", balance)) 
            if capital_input > 0:
                session["capital"] = capital_input
                session.modified = True
                balance = capital_input 
        except ValueError:
            pass 

        # Parse ALL form inputs for calculation
        symbol = form.get("symbol", selected_symbol)
        side = form.get("side", default_side)
        entry = float(form.get("entry") or default_entry)
        sl_type = form.get("sl_type", default_sl_type)
        sl_value = float(form.get("sl_value") or default_sl_value)

        user_units = float(form.get("user_units") or 0.0)
        user_lev = float(form.get("user_lev") or 0.0)
        
        # TP Inputs
        tp1_price = float(form.get("tp1_price") or 0.0)
        tp1_percent = float(form.get("tp1_percent") or 0.0)
        tp2_price = float(form.get("tp2_price") or 0.0)
        tp_list = calculate_targets_from_form(tp1_price, tp1_percent, tp2_price)

        # --- Sizing Calculation (run for preview/validation) ---
        sizing = calculate_position_sizing(
            balance,
            entry,
            sl_type,
            sl_value
        )
        
        # --- Execute Trade ---
        if 'place_order' in form or (sizing and sizing.get("error") is None and form.get("sl_type") is not None): 
            
            resp = execute_trade_action(
                balance=balance,
                symbol=symbol,
                side=side,
                entry=entry,
                sl_type=sl_type, 
                sl_value=sl_value, 
                order_type=default_order_type,
                tp_list=tp_list,
                sizing=sizing,
                user_units=user_units,
                user_lev=user_lev,
            )

            trade_status = resp

    # --------------------------
    # GET or POST Final Preview
    # --------------------------
    if not sizing:
        sizing = calculate_position_sizing(
            balance,
            float(request.form.get("entry", default_entry)),
            request.form.get("sl_type", default_sl_type),
            float(request.form.get("sl_value", default_sl_value))
        )
        
    # Variables for template persistence
    selected_symbol = request.form.get("symbol", "BTCUSD")
    
    # --------------------------
    # RENDER TEMPLATE
    # --------------------------
    tv_symbol = f"BINANCE:{selected_symbol}"
    chart_html = generate_chart_html(tv_symbol)

    return render_template(
        "index.html",
        trade_status=trade_status,
        sizing=sizing,
        stats=stats_today,
        trades=session["trades"],
        chart_html=chart_html,
        
        balance=balance,
        margin_used=margin_used,
        symbols=BROKER_SYMBOLS,
        today=datetime.utcnow(),

        # 🔥 FIX ADDED HERE
        datetime=datetime,

        selected_symbol=selected_symbol,
        default_entry=request.form.get("entry", 27050.0),
        default_sl_value=request.form.get("sl_value", 100.0),
        default_sl_type=request.form.get("sl_type", "SL Points"),
        default_side=request.form.get("side", "LONG"),
        default_units=request.form.get("user_units", 0.0),
        default_lev=request.form.get("user_lev", 0.0),

        daily_max_trades=DAILY_MAX_TRADES,
        daily_max_per_symbol=DAILY_MAX_PER_SYMBOL,
    )


@app.route("/log")
def trade_log():
    trades = session["trades"]
    today = datetime.utcnow().date().isoformat()
    today_trades = [t for t in trades if t["date"] == today]
    return render_template("trade_log.html", trades=today_trades)


if __name__ == "__main__":
    app.run(debug=True)
