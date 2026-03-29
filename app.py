from flask import Flask, render_template, request, session, jsonify, redirect, url_for, Response, flash
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from authlib.integrations.flask_client import OAuth
from dotenv import load_dotenv
from datetime import datetime, timedelta
from functools import wraps
from models import db, User, ExchangeConnection, SubscriptionHistory
import logic
import config
import os
import csv
import io
import uuid
import razorpay

# Load environment variables
load_dotenv()

app = Flask(__name__)

# Secure secret key
app.secret_key = os.getenv('SECRET_KEY', os.urandom(32).hex())

# Session configuration
app.config['SESSION_PERMANENT'] = True
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)
app.config['SESSION_COOKIE_SECURE'] = os.getenv('SESSION_COOKIE_SECURE', 'False').lower() == 'true'
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'sqlite:///users.db').replace("postgres://", "postgresql://", 1)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

RAZORPAY_MONTHLY_PLAN_ID = config.RAZORPAY_MONTHLY_PLAN_ID
RAZORPAY_YEARLY_PLAN_ID = config.RAZORPAY_YEARLY_PLAN_ID

db.init_app(app)
login_manager = LoginManager()
login_manager.login_view = 'login'
login_manager.init_app(app)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

oauth = OAuth(app)
google = oauth.register(
    name='google',
    client_id=os.getenv('GOOGLE_CLIENT_ID'),
    client_secret=os.getenv('GOOGLE_CLIENT_SECRET'),
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'}
)

razorpay_client = razorpay.Client(auth=(config.RAZORPAY_KEY_ID, config.RAZORPAY_KEY_SECRET))

def subscription_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for('login'))
        admin_emails = ['admin@mindriskcontrol.com', 'test@test.com']
        if current_user.email.lower() in [email.lower() for email in admin_emails]:
            return f(*args, **kwargs)
        if not current_user.is_subscribed:
            flash("Please subscribe to access the trading dashboard.", "warning")
            return redirect(url_for('subscribe'))
        if current_user.subscription_end and datetime.utcnow() > current_user.subscription_end:
            current_user.is_subscribed = False
            current_user.subscription_status = 'expired'
            db.session.commit()
            flash("Your subscription has expired.", "warning")
            return redirect(url_for('subscribe'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/')
def home():
    return render_template('home.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = (request.form.get('email') or '').strip().lower()
        username = (request.form.get('username') or '').strip()
        password = request.form.get('password') or ''
        if User.query.filter_by(email=email).first():
            flash('Email already registered.', 'error')
            return render_template('register.html')
        hashed_pw = generate_password_hash(password)
        new_user = User(username=username, email=email, password=hashed_pw)
        db.session.add(new_user)
        db.session.commit()
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password')
        user = User.query.filter_by(email=email).first()
        if not user or not check_password_hash(user.password, password):
            flash("Invalid email or password", "error")
            return render_template('login.html')
        login_user(user, remember=True)
        return redirect(url_for('index'))
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    session.clear()
    return redirect(url_for('login'))

@app.route('/index', methods=["GET", "POST"])
@login_required
@subscription_required
def index():
    logic.initialize_session()
    symbols = logic.get_all_exchange_symbols(current_user.id)
    
    # --- ROBUST BALANCE UNPACKING (FIX FOR 0.0 BALANCE) ---
    # logic.get_live_balance returns: ( (balance, margin), details_dict )
    balance_result = logic.get_live_balance(current_user.id)
    balance = 0.0
    margin_used = 0.0

    if balance_result and isinstance(balance_result, (tuple, list)):
        # Try primary unpacking from the tuple (part 0)
        stats = balance_result[0]
        if isinstance(stats, (tuple, list)) and len(stats) >= 2:
            balance = float(stats[0] or 0.0)
            margin_used = float(stats[1] or 0.0)
        
        # Fallback: Try unpacking from the dictionary (part 1) if balance is still 0
        if balance == 0.0 and len(balance_result) > 1:
            details = balance_result[1]
            if isinstance(details, dict):
                balance = float(details.get('total_balance', 0.0))
                margin_used = float(details.get('total_margin', 0.0))
    
    unutilized = max(balance - margin_used, 0.0)
    # ------------------------------------------------------

    selected_symbol = request.form.get("symbol", "BTCUSDT")
    side = request.form.get("side", "LONG")
    order_type = request.form.get("order_type", "MARKET")
    margin_mode = request.form.get("margin_mode", "ISOLATED")

    # Entry price defaults to live price
    current_price = logic.get_live_price(selected_symbol, current_user.id)
    entry = float(request.form.get("entry") or current_price or 0)
    
    sl_type = request.form.get("sl_type", "SL % Movement")
    sl_val = float(request.form.get("sl_value") or 0)
    tp1 = float(request.form.get("tp1") or 0)
    tp1_pct = float(request.form.get("tp1_pct") or 0)
    tp2 = float(request.form.get("tp2") or 0)

    sizing = logic.calculate_position_sizing(unutilized, entry, sl_type, sl_val)
    trade_status = session.pop("trade_status", None)

    if request.method == "POST" and "place_order" in request.form and not sizing.get("error"):
        result = logic.execute_trade_action(
            balance, selected_symbol, side, entry, order_type, sl_type, sl_val, sizing,
            float(request.form.get("user_units") or 0), float(request.form.get("user_lev") or 0),
            margin_mode, tp1, tp1_pct, tp2, current_user.id
        )
        session["trade_status"] = result
        return redirect(url_for("index"))
    
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
        today_stats=logic.get_today_stats()
    )

# Exchange connection routes
@app.route('/exchange-connections')
@login_required
@subscription_required
def exchange_connections():
    connections = ExchangeConnection.query.filter_by(user_id=current_user.id).all()
    return render_template('exchange_connections.html', connections=connections, supported_exchanges=config.SUPPORTED_EXCHANGES)

@app.route('/add-exchange', methods=['POST'])
@login_required
def add_exchange():
    data = request.get_json()
    conn = ExchangeConnection(
        user_id=current_user.id,
        exchange_type=data.get('exchange_type'),
        api_key=data.get('api_key').strip(),
        api_secret=data.get('api_secret').strip(),
        connection_name=data.get('connection_name') or "Binance Account",
        is_connected=True
    )
    db.session.add(conn)
    db.session.commit()
    logic.clear_user_client(current_user.id)
    return jsonify({'success': True})

@app.route('/disconnect-exchange/<int:connection_id>', methods=['POST'])
@login_required
def disconnect_exchange(connection_id):
    conn = ExchangeConnection.query.get_or_404(connection_id)
    if conn.user_id == current_user.id:
        db.session.delete(conn)
        db.session.commit()
        logic.clear_user_client(current_user.id)
    return jsonify({'success': True})

# API Endpoints
@app.route("/api/wallet")
@login_required
def get_wallet_api():
    return jsonify(logic.get_wallet_balances(current_user.id))

@app.route("/get_live_price/<symbol>")
@login_required
def live_price_api(symbol):
    return jsonify({"price": logic.get_live_price(symbol, current_user.id)})

@app.route("/get_open_positions")
@login_required
def get_open_positions_api():
    return jsonify({"positions": logic.get_open_positions(current_user.id)})

@app.route("/test-binance")
@login_required
def test_binance():
    client = logic.get_client(current_user.id)
    balance_data = logic.get_live_balance(current_user.id)
    # Ensure test-binance also uses robust unpacking for the report
    bal = 0.0
    if balance_data and balance_data[0]:
        bal = balance_data[0][0]
    
    return jsonify({
        'status': 'success',
        'client_available': client is not None,
        'balance': bal,
        'btc_price': logic.get_live_price('BTCUSDT', current_user.id),
        'margin_used': balance_result[1] if balance_result and len(balance_result)>1 else {}
    })

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(host='0.0.0.0', port=5000, debug=True)