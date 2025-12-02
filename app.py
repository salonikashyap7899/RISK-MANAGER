# app.py
from flask import Flask, render_template, request, redirect, url_for, session
from datetime import datetime
from logic import (
    initialize_session,
    calculate_position_sizing,
    execute_trade_action,
)
from calculations import calculate_targets_from_form

app = Flask(__name__)
app.secret_key = "replace-this-secret-key"


@app.before_request
def before_request():
    initialize_session()


@app.route("/", methods=["GET", "POST"])
def index():
    # Session store for trades & stats
    trades = session["trades"]
    stats = session["stats"]

    today = datetime.utcnow().date().isoformat()
    stats_today = stats.get(today, {"total": 0, "by_symbol": {}})

    trade_status = None
    sizing = None

    if request.method == "POST":
        form = request.form

        # Parse form inputs
        symbol = form.get("symbol")
        side = form.get("side")
        order_type = form.get("order_type")  # market/limit/stop_market/stop_limit
        entry = float(form.get("entry") or 0.0)

        sl_type = form.get("sl_type")  # 'points' or 'percent'
        sl_value = float(form.get("sl_value") or 0.0)

        # user provided units/leverage
        user_units = float(form.get("user_units") or 0.0)
        user_lev = float(form.get("user_lev") or 0.0)

        # TP fields
        tp1_price = float(form.get("tp1_price") or 0.0)
        tp1_percent = int(form.get("tp1_percent") or 0)
        tp2_price = float(form.get("tp2_price") or 0.0)
        tp_list = calculate_targets_from_form(tp1_price, tp1_percent, tp2_price)

        # Calculate suggested sizing for display BEFORE executing (safe readonly)
        sizing = calculate_position_sizing(session.get("capital", 10000.0), entry, sl_type, sl_value)

        # Execute/validate trade
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
            api_key=None,
            api_secret=None
        )

        trade_status = resp

    return render_template(
        "index.html",
        trade_status=trade_status,
        sizing=sizing,
        stats=stats_today,
    )


@app.route("/log")
def trade_log():
    trades = session["trades"]
    today = datetime.utcnow().date().isoformat()
    today_trades = [t for t in trades if t["date"] == today]
    return render_template("trade_log.html", trades=today_trades)


if __name__ == "__main__":
    app.run(debug=True)
