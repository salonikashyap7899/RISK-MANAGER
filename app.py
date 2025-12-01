from flask import Flask, render_template, request
from datetime import datetime
from math import ceil
import pandas as pd
import plotly.graph_objects as go
import numpy as np

# Flask App
app = Flask(__name__)

# Session Simulation (instead of Streamlit session)
session_state = {"trades": [], "stats": {}}

# Constants
DAILY_MAX_TRADES = 4
DAILY_MAX_PER_SYMBOL = 2
RISK_PERCENT = 1.0
DEFAULT_BALANCE = 10000.00
DEFAULT_SL_POINTS_BUFFER = 20.0
DEFAULT_SL_PERCENT_BUFFER = 0.2

API_KEY = "TEST"
API_SECRET = "TEST"


# ==============================
#   STATE INITIALIZER
# ==============================
def initialize_state():
    today = datetime.utcnow().date().isoformat()
    if today not in session_state["stats"]:
        session_state["stats"][today] = {"total": 0, "by_symbol": {}}


# ==============================
#   CAPITAL CALCULATION
# ==============================
def calculate_unutilized_capital(balance):
    today = datetime.utcnow().date().isoformat()
    today_trades = [t for t in session_state["trades"] if t["date"] == today]
    used_capital = sum(t["notional"] / t["leverage"] for t in today_trades)
    return max(0, balance - used_capital)


def get_account_balance(a, b):
    return DEFAULT_BALANCE


# ==============================
#   POSITION SIZE ENGINE
# ==============================
def calculate_position_sizing(balance, symbol, entry, sl_type, sl_value):

    unutilized = calculate_unutilized_capital(balance)
    risk_amount = (unutilized * RISK_PERCENT) / 100.0

    if unutilized <= 0 or entry <= 0:
        return 0, 0, 0, 0, 0, "ERR"

    # SL POINTS
    if sl_type == "SL POINTS":
        distance = sl_value
        if distance > 0:
            units = risk_amount / distance
            notional = units * entry
            lev = notional / unutilized
            lev = max(1, ceil(lev * 2) / 2)
            return units, lev, notional, unutilized, 0, "OK"

    # SL % MOVE
    else:
        sl_decimal = sl_value / 100.0
        eff = sl_decimal + (DEFAULT_SL_PERCENT_BUFFER / 100.0)
        if eff > 0:
            units = risk_amount / (eff * entry)
            notional = units * entry
            lev = max(1, ceil((notional / unutilized) * 2) / 2)
            return units, lev, notional, unutilized, 0, "OK"

    return 0, 0, 0, 0, 0, "ERR"


# ==============================
#   EXECUTE TRADE
# ==============================
def execute_trade(balance, symbol, side, entry, sl, units, lev, usr_units, usr_lev, sl_type, sl_value, order_type):

    now = datetime.utcnow()

    f_units = usr_units if usr_units > 0 else units
    f_lev = usr_lev if usr_lev > 0 else lev
    notional = f_units * entry

    # Save trade
    trade = {
        "id": int(now.timestamp() * 1000),
        "date": now.date().isoformat(),
        "time": now.strftime("%H:%M:%S"),
        "symbol": symbol,
        "side": side,
        "entry": entry,
        "units": f_units,
        "leverage": f_lev,
        "notional": notional,
        "sl": sl
    }

    session_state["trades"].append(trade)

    today = now.date().isoformat()
    session_state["stats"].setdefault(today, {"total": 0, "by_symbol": {}})
    session_state["stats"][today]["total"] += 1
    session_state["stats"][today]["by_symbol"][symbol] = \
        session_state["stats"][today]["by_symbol"].get(symbol, 0) + 1

    return "Order Executed Successfully."


# ==============================
#   PLOTLY CANDLESTICK CHART
# ==============================
def generate_chart(symbol="BTCUSD"):
    dates = pd.date_range(end=datetime.utcnow(), periods=60).tolist()
    prices = np.linspace(26500, 27200, 60) + np.random.randint(-200, 200, 60)
    opens = prices + np.random.randint(-80, 80, 60)
    highs = prices + np.random.randint(10, 120, 60)
    lows = prices - np.random.randint(10, 120, 60)
    volume = np.random.randint(2000, 9800, 60)

    fig = go.Figure()

    fig.add_trace(go.Candlestick(
        x=dates,
        open=opens,
        high=highs,
        low=lows,
        close=prices,
        name=symbol
    ))

    fig.add_trace(go.Bar(
        x=dates,
        y=volume,
        name="Volume",
        marker=dict(opacity=0.3)
    ))

    fig.update_layout(
        template="plotly_dark",
        height=330,
        margin=dict(l=0, r=0, t=20, b=0),
        paper_bgcolor="#0b0f12",
        plot_bgcolor="#0b0f12",
        xaxis=dict(showgrid=False),
        yaxis=dict(showgrid=True, gridcolor="#222"),
        font=dict(color="#bbbbbb")
    )

    return fig.to_html(full_html=False)


# ==============================
#   FLASK ROUTES
# ==============================
@app.route("/", methods=["GET", "POST"])
def index():
    initialize_state()
    balance = get_account_balance(API_KEY, API_SECRET)

    trade_status = ""
    chart_html = generate_chart()

    if request.method == "POST":

        symbol = request.form["symbol"]
        side = request.form["side"]
        order_type = request.form["order_type"]
        entry = float(request.form["entry"])
        sl_type = request.form["sl_type"]
        sl_value = float(request.form["sl_value"])
        sl = float(request.form["sl"])
        user_units = float(request.form["user_units"])
        user_lev = float(request.form["user_lev"])

        units, lev, notional, unused, max_lev, msg = calculate_position_sizing(
            balance, symbol, entry, sl_type, sl_value
        )

        trade_status = execute_trade(
            balance, symbol, side, entry, sl,
            units, lev, user_units, user_lev,
            sl_type, sl_value, order_type
        )

    today = datetime.utcnow().date().isoformat()
    today_trades = [t for t in session_state["trades"] if t["date"] == today]

    return render_template("index.html",
                           balance=balance,
                           trades=today_trades,
                           trade_status=trade_status,
                           chart_html=chart_html)


if __name__ == "__main__":
    app.run(debug=True)
