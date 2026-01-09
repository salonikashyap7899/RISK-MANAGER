from flask import Flask, render_template, request, session, jsonify, redirect, url_for, Response
from datetime import datetime
import logic
import os
import csv
import io

app = Flask(__name__)
app.secret_key = "trading_secret_key_ultra_secure_2025"

# Use simple client-side sessions
app.config['SESSION_PERMANENT'] = False

@app.route("/get_live_price/<symbol>")
def live_price_api(symbol):
    """Get live price for a symbol"""
    price = logic.get_live_price(symbol)
    return jsonify({"price": price if price else 0})

@app.route("/get_open_positions")
def get_open_positions_api():
    """FIX #2: Returns REAL live positions from Binance with timestamps"""
    positions = logic.get_open_positions()
    return jsonify({"positions": positions})

@app.route("/get_trade_history")
def get_trade_history_api():
    """FIX #2: Get REAL trade history from Binance"""
    trades = logic.get_trade_history()
    return jsonify({"trades": trades})

@app.route("/get_today_stats")
def get_today_stats_api():
    """FIX #1: Get today's trade statistics for limit display"""
    stats = logic.get_today_stats()
    return jsonify(stats)

@app.route("/close_position/<symbol>", methods=["POST"])
def close_position_api(symbol):
    """Close entire position for a symbol"""
    result = logic.close_position(symbol)
    return jsonify(result)

@app.route("/partial_close", methods=["POST"])
def partial_close_api():
    """FIX #5: Partial close position"""
    data = request.get_json()
    symbol = data.get('symbol')
    close_percent = data.get('close_percent')
    close_qty = data.get('close_qty')
    
    if not symbol:
        return jsonify({"success": False, "message": "Symbol required"})
    
    result = logic.partial_close_position(symbol, close_percent, close_qty)
    return jsonify(result)

@app.route("/update_sl", methods=["POST"])
def update_sl_api():
    """FIX #6: Update stop loss with -1% to 0% restriction"""
    data = request.get_json()
    symbol = data.get('symbol')
    new_sl_percent = float(data.get('new_sl_percent', 0))
    
    if not symbol:
        return jsonify({"success": False, "message": "Symbol required"})
    
    result = logic.update_stop_loss(symbol, new_sl_percent)
    return jsonify(result)

@app.route("/download_trades")
def download_trades():
    """Download trade history as CSV"""
    trades = logic.get_trade_history()
    
    # Create CSV in memory
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Write header
    writer.writerow([
        'Time (UTC)', 'Symbol', 'Side', 'Quantity', 
        'Price', 'Realized PnL', 'Commission'
    ])
    
    # Write trade data
    for trade in trades:
        writer.writerow([
            trade.get('time', ''),
            trade.get('symbol', ''),
            trade.get('side', ''),
            trade.get('qty', ''),
            trade.get('price', ''),
            trade.get('realized_pnl', ''),
            trade.get('commission', '')
        ])
    
    # Prepare response
    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={
            'Content-Disposition': f'attachment; filename=trade_history_{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}.csv'
        }
    )

@app.route("/", methods=["GET", "POST"])
def index():
    logic.initialize_session()

    symbols = logic.get_all_exchange_symbols()
    live_bal, live_margin = logic.get_live_balance()

    balance = live_bal or 0.0
    margin_used = live_margin or 0.0
    unutilized = max(balance - margin_used, 0.0)

    selected_symbol = request.form.get("symbol", "BTCUSDT")
    side = request.form.get("side", "LONG")
    order_type = request.form.get("order_type", "MARKET")
    margin_mode = request.form.get("margin_mode", "ISOLATED")

    entry = float(request.form.get("entry") or logic.get_live_price(selected_symbol) or 0)
    sl_type = request.form.get("sl_type", "SL % Movement")
    sl_val = float(request.form.get("sl_value") or 0)

    # TP Variables
    tp1 = float(request.form.get("tp1") or 0)
    tp1_pct = float(request.form.get("tp1_pct") or 0)
    tp2 = float(request.form.get("tp2") or 0)

    sizing = logic.calculate_position_sizing(unutilized, entry, sl_type, sl_val)
    trade_status = session.pop("trade_status", None)

    if request.method == "POST" and "place_order" in request.form and not sizing.get("error"):
        result = logic.execute_trade_action(
            balance,
            selected_symbol,
            side,
            entry,
            order_type,
            sl_type,
            sl_val,
            sizing,
            float(request.form.get("user_units") or 0),
            float(request.form.get("user_lev") or 0),
            margin_mode,
            tp1,
            tp1_pct,
            tp2
        )
        session["trade_status"] = result
        session.modified = True
        return redirect(url_for("index"))
    
    # Get today's stats for display
    today_stats = logic.get_today_stats()
    
    return render_template(
        "index.html",
        trade_status=trade_status,
        sizing=sizing,
        balance=round(balance, 2),
        unutilized=round(unutilized, 2),
        symbols=symbols,
        selected_symbol=selected_symbol,
        default_entry=entry,
        default_sl_value=sl_val,
        default_sl_type=sl_type,
        default_side=side,
        order_type=order_type,
        margin_mode=margin_mode,
        tp1=tp1,
        tp1_pct=tp1_pct,
        tp2=tp2,
        today_stats=today_stats
    )

if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=5000,
        debug=False,
        use_reloader=False,
        threaded=False
    )
