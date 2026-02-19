from flask import Flask, render_template, request, session, jsonify, redirect, url_for, Response, flash
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from authlib.integrations.flask_client import OAuth
from dotenv import load_dotenv
from datetime import datetime
from functools import wraps
import razorpay
import logic
import os
import csv
import io

from models import db, User

load_dotenv()

app = Flask(__name__)

# 🔐 SECURITY FIX — No default secret fallback
app.secret_key = os.getenv("SECRET_KEY")

app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'sqlite:///users.db').replace("postgres://", "postgresql://", 1)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SESSION_PERMANENT'] = False

db.init_app(app)

login_manager = LoginManager()
login_manager.login_view = 'login'
login_manager.init_app(app)

razorpay_client = razorpay.Client(
    auth=(os.getenv('RAZORPAY_KEY_ID'), os.getenv('RAZORPAY_KEY_SECRET'))
)

oauth = OAuth(app)
google = oauth.register(
    name='google',
    client_id=os.getenv('GOOGLE_CLIENT_ID'),
    client_secret=os.getenv('GOOGLE_CLIENT_SECRET'),
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'}
)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# ---------------- SUBSCRIPTION DECORATOR ----------------

def subscription_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_subscribed:
            flash('Please subscribe to access the trading dashboard.', 'warning')
            return redirect(url_for('subscribe'))
        return f(*args, **kwargs)
    return decorated_function


# ---------------- SUBSCRIPTION ROUTES ----------------

@app.route('/subscribe')
@login_required
def subscribe():
    return render_template('subscribe.html', key_id=os.getenv('RAZORPAY_KEY_ID'))


@app.route('/create-subscription', methods=['POST'])
@login_required
def create_subscription():
    try:
        subscription_data = {
            "plan_id": os.getenv("RAZORPAY_PLAN_ID"),
            "total_count": 12,
            "quantity": 1,
            "customer_notify": 1,
            "notes": {
                "user_id": current_user.id,
                "email": current_user.email
            }
        }

        subscription = razorpay_client.subscription.create(subscription_data)
        current_user.razorpay_subscription_id = subscription['id']
        db.session.commit()

        return jsonify({
            "subscription_id": subscription['id'],
            "status": "created"
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/payment-success', methods=['POST'])
@login_required
def payment_success():
    data = request.json

    try:
        params_dict = {
            'razorpay_payment_id': data.get('razorpay_payment_id'),
            'razorpay_subscription_id': data.get('razorpay_subscription_id'),
            'razorpay_signature': data.get('razorpay_signature')
        }

        razorpay_client.utility.verify_subscription_payment_signature(params_dict)

        current_user.is_subscribed = True
        current_user.subscription_status = 'active'
        current_user.razorpay_payment_id = data.get('razorpay_payment_id')
        current_user.razorpay_subscription_id = data.get('razorpay_subscription_id')
        current_user.subscription_start = datetime.utcnow()

        db.session.commit()
        return jsonify({"success": True})

    except razorpay.errors.SignatureVerificationError:
        return jsonify({"success": False, "message": "Invalid Signature"}), 400
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


# 🔥 PRODUCTION FIX — Razorpay Webhook
@app.route('/razorpay-webhook', methods=['POST'])
def razorpay_webhook():
    data = request.json

    if data.get('event') == 'subscription.cancelled':
        sub_id = data['payload']['subscription']['entity']['id']
        user = User.query.filter_by(razorpay_subscription_id=sub_id).first()

        if user:
            user.is_subscribed = False
            user.subscription_status = "cancelled"
            db.session.commit()

    return jsonify({"status": "ok"})


# ---------------- AUTH ROUTES ----------------

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = request.form.get('email')

        # ✅ Duplicate email fix
        existing_user = User.query.filter_by(email=email).first()
        if existing_user:
            flash("Email already registered", "danger")
            return redirect(url_for('register'))

        hashed_pw = generate_password_hash(request.form.get('password'))

        new_user = User(
            username=request.form.get('username'),
            email=email,
            password=hashed_pw
        )

        db.session.add(new_user)
        db.session.commit()

        flash("Registration successful. Please login.", "success")
        return redirect(url_for('login'))

    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(email=request.form.get('email')).first()

        if user and check_password_hash(user.password, request.form.get('password')):
            login_user(user)

            if user.is_subscribed:
                return redirect(url_for('index'))
            else:
                return redirect(url_for('subscribe'))
        else:
            flash("Invalid email or password", "danger")

    return render_template('login.html')


@app.route('/login/google')
def google_login():
    return google.authorize_redirect(url_for('google_authorize', _external=True))


@app.route('/authorize/google')
def google_authorize():
    token = google.authorize_access_token()
    user_info = token.get('userinfo')

    user = User.query.filter_by(email=user_info['email']).first()

    if not user:
        user = User(
            username=user_info['name'],
            email=user_info['email'],
            google_id=user_info['sub']
        )
        db.session.add(user)
        db.session.commit()

    login_user(user)

    if user.is_subscribed:
        return redirect(url_for('index'))
    else:
        return redirect(url_for('subscribe'))


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))


# ---------------- TRADING ROUTES ----------------
# 🔥 All now subscription protected

@app.route("/get_live_price/<symbol>")
@login_required
@subscription_required
def live_price_api(symbol):
    price = logic.get_live_price(symbol)
    return jsonify({"price": price if price else 0})


@app.route("/get_open_positions")
@login_required
@subscription_required
def get_open_positions_api():
    return jsonify({"positions": logic.get_open_positions()})


@app.route("/get_trade_history")
@login_required
@subscription_required
def get_trade_history_api():
    return jsonify({"trades": logic.get_trade_history()})


@app.route("/get_today_stats")
@login_required
@subscription_required
def get_today_stats_api():
    return jsonify(logic.get_today_stats())


@app.route("/close_position/<symbol>", methods=["POST"])
@login_required
@subscription_required
def close_position_api(symbol):
    return jsonify(logic.close_position(symbol))


@app.route("/partial_close", methods=["POST"])
@login_required
@subscription_required
def partial_close_api():
    data = request.get_json()
    symbol = data.get('symbol')
    close_percent = data.get('close_percent')
    close_qty = data.get('close_qty')

    if not symbol:
        return jsonify({"success": False, "message": "Symbol required"})

    return jsonify(logic.partial_close_position(symbol, close_percent, close_qty))


@app.route("/update_sl", methods=["POST"])
@login_required
@subscription_required
def update_sl_api():
    data = request.get_json()
    symbol = data.get('symbol')
    new_sl_percent = float(data.get('new_sl_percent', 0))

    if not symbol:
        return jsonify({"success": False, "message": "Symbol required"})

    return jsonify(logic.update_stop_loss(symbol, new_sl_percent))


@app.route("/download_trades")
@login_required
@subscription_required
def download_trades():
    trades = logic.get_trade_history()
    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(['Time (UTC)', 'Symbol', 'Side', 'Quantity', 'Price', 'Realized PnL', 'Commission', 'Order ID'])

    for trade in trades:
        writer.writerow([
            trade.get('time', ''),
            trade.get('symbol', ''),
            trade.get('side', ''),
            trade.get('qty', ''),
            trade.get('price', ''),
            trade.get('realized_pnl', ''),
            trade.get('commission', ''),
            trade.get('order_id', '')
        ])

    output.seek(0)

    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename=trade_history_{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}.csv'}
    )


# ---------------- MAIN DASHBOARD ----------------

@app.route("/", methods=["GET", "POST"])
@login_required
@subscription_required
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

    tp1 = float(request.form.get("tp1") or 0)
    tp1_pct = float(request.form.get("tp1_pct") or 0)
    tp2 = float(request.form.get("tp2") or 0)

    sizing = logic.calculate_position_sizing(unutilized, entry, sl_type, sl_val)
    trade_status = session.pop("trade_status", None)

    if request.method == "POST" and "place_order" in request.form and not sizing.get("error"):
        result = logic.execute_trade_action(
            balance, selected_symbol, side, entry, order_type, sl_type, sl_val,
            sizing,
            float(request.form.get("user_units") or 0),
            float(request.form.get("user_lev") or 0),
            margin_mode, tp1, tp1_pct, tp2
        )

        session["trade_status"] = result
        session.modified = True
        return redirect(url_for("index"))

    today_stats = logic.get_today_stats()

    return render_template(
        "index.html",
        user=current_user,
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


# ---------------- APP RUN ----------------

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(host='0.0.0.0', port=5000)
