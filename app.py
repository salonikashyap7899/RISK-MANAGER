from flask import Flask, render_template, request, session, jsonify, redirect, url_for, Response, flash
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from authlib.integrations.flask_client import OAuth
from dotenv import load_dotenv
from datetime import datetime, timedelta
from functools import wraps
from models import db, User, ExchangeConnection, SubscriptionHistory, TradeDailyStats, TradeLog
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

# Secure secret key - use environment variable or generate one
app.secret_key = os.getenv('SECRET_KEY', os.urandom(32).hex())

# Session configuration for persistent login
app.config['SESSION_PERMANENT'] = True
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)
app.config['SESSION_COOKIE_SECURE'] = os.getenv('SESSION_COOKIE_SECURE', 'False').lower() == 'true'
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

instance_path = os.path.join(app.root_path, 'instance')
os.makedirs(instance_path, exist_ok=True)
db_file_path = os.path.join(instance_path, 'users.db')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.abspath(db_file_path).replace('\\', '/')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

RAZORPAY_MONTHLY_PLAN_ID = config.RAZORPAY_MONTHLY_PLAN_ID
RAZORPAY_YEARLY_PLAN_ID = config.RAZORPAY_YEARLY_PLAN_ID

db.init_app(app)
login_manager = LoginManager()
login_manager.login_view = 'login'
login_manager.init_app(app)

@login_manager.user_loader
def load_user(user_id):
    try:
        return User.query.get(int(user_id))
    except Exception:
        return None

oauth = OAuth(app)
google = oauth.register(
    name='google',
    client_id=os.getenv('GOOGLE_CLIENT_ID'),
    client_secret=os.getenv('GOOGLE_CLIENT_SECRET'),
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'}
)

razorpay_client = razorpay.Client(auth=(config.RAZORPAY_KEY_ID, config.RAZORPAY_KEY_SECRET))

def get_month_end(dt=None):
    if not dt:
        dt = datetime.utcnow()
    next_month = dt.replace(day=28) + timedelta(days=4)
    return next_month.replace(day=1) - timedelta(seconds=1)

def subscription_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        try:
            if not current_user.is_authenticated:
                return redirect(url_for('login'))
        except Exception:
            return redirect(url_for('login'))
        
        # Admin bypass
        if getattr(current_user, 'is_admin', False):
            return f(*args, **kwargs)
        
        ADMIN_EMAILS = ['admin@mindriskcontrol.com', 'test@test.com']
        if current_user.email.lower() in [email.lower() for email in ADMIN_EMAILS]:
            return f(*args, **kwargs)
        
        now = datetime.utcnow()
        
        if not current_user.is_subscribed:
            flash("Please subscribe to access the trading dashboard.", "warning")
            return redirect(url_for('subscribe'))
        
        if current_user.subscription_end:
            if now > current_user.subscription_end:
                current_user.is_subscribed = False
                current_user.subscription_status = 'expired'
                db.session.commit()
                flash("Your subscription has expired. Please renew to access the dashboard.", "warning")
                return redirect(url_for('subscribe'))
            
        return f(*args, **kwargs)
    return decorated_function

@app.route('/')
def home():
    return render_template('home.html')

@app.route('/home')
def home_alias():
    return redirect(url_for('home'))

@app.route('/about')
def about():
    return render_template('about.html')

@app.route('/contact')
def contact():
    return render_template('contact.html')

@app.route('/terms')
def terms():
    return render_template('terms.html')

@app.route('/privacy')
def privacy():
    return render_template('privacy.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = (request.form.get('email') or '').strip().lower()
        username = (request.form.get('username') or '').strip()
        password = request.form.get('password') or ''
        confirm_password = request.form.get('confirm_password') or ''

        if password != confirm_password:
            msg = 'Passwords do not match.'
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({'success': False, 'message': msg}), 400
            flash(msg, 'error')
            return render_template('register.html'), 400

        if User.query.filter_by(email=email).first():
            msg = 'Email already registered. Please log in or use another email.'
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({'success': False, 'message': msg}), 400
            flash(msg, 'error')
            return render_template('register.html'), 400

        if User.query.filter_by(username=username).first():
            msg = 'Username already taken. Choose a different username.'
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({'success': False, 'message': msg}), 400
            flash(msg, 'error')
            return render_template('register.html'), 400

        hashed_pw = generate_password_hash(password)
        new_user = User(
            username=username,
            email=email,
            password=hashed_pw
        )
        try:
            db.session.add(new_user)
            db.session.commit()
            msg = 'Registration successful. Please log in.'
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({'success': True, 'message': msg}), 200
            flash(msg, 'success')
            return redirect(url_for('login'))
        except Exception as e:
            db.session.rollback()
            msg = 'An error occurred during registration. Please try again.'
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({'success': False, 'message': msg}), 500
            flash(msg, 'error')
            return render_template('register.html'), 500

    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password')

        user = User.query.filter_by(email=email).first()

        if not user or not check_password_hash(user.password, password):
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({'success': False, 'message': 'Invalid email or password'}), 401
            flash("Invalid email or password", "error")
            return render_template('login.html'), 401

        login_user(user, remember=True)
        session_id = str(uuid.uuid4())
        session['session_id'] = session_id
        session.permanent = True
        user.active_session = session_id
        db.session.commit()

        is_admin = getattr(user, 'is_admin', False) or user.email.lower() in ['admin@mindriskcontrol.com', 'test@test.com']

        if not is_admin:
            if not user.is_subscribed:
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return jsonify({'success': False, 'message': 'Please subscribe to access the dashboard'}), 200
                flash("Please subscribe to access the trading dashboard.", "warning")
                return redirect(url_for('subscribe'))

            if user.subscription_end:
                if datetime.utcnow() > user.subscription_end:
                    user.is_subscribed = False
                    user.subscription_status = 'expired'
                    db.session.commit()
                    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                        return jsonify({'success': False, 'message': 'Subscription expired'}), 200
                    flash("Your subscription has expired. Please renew.", "warning")
                    return redirect(url_for('subscribe'))
        
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'success': True, 'redirect': url_for('index')}), 200
        
        return redirect(url_for('index'))

    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for('home'))

@app.route('/google-login')
def google_login():
    redirect_uri = url_for('google_authorize', _external=True)
    return google.authorize_redirect(redirect_uri)

@app.route('/google-authorize')
def google_authorize():
    try:
        token = google.authorize_access_token()
        user_info = token.get('userinfo')
        
        if not user_info:
            flash("Failed to get user information from Google.", "error")
            return redirect(url_for('login'))
        
        email = user_info.get('email')
        google_id = user_info.get('sub')
        name = user_info.get('name', email.split('@')[0])
        
        user = User.query.filter_by(email=email).first()
        
        if not user:
            user = User(
                email=email,
                username=name,
                google_id=google_id,
                password=generate_password_hash(os.urandom(24).hex())
            )
            db.session.add(user)
            db.session.commit()
        elif not user.google_id:
            user.google_id = google_id
            db.session.commit()
        
        login_user(user, remember=True)
        session_id = str(uuid.uuid4())
        session['session_id'] = session_id
        session.permanent = True
        user.active_session = session_id
        db.session.commit()
        
        is_admin = getattr(user, 'is_admin', False) or user.email.lower() in ['admin@mindriskcontrol.com', 'test@test.com']
        
        if not is_admin and not user.is_subscribed:
            flash("Please subscribe to access the trading dashboard.", "warning")
            return redirect(url_for('subscribe'))
        
        return redirect(url_for('index'))
        
    except Exception as e:
        flash(f"Authentication failed: {str(e)}", "error")
        return redirect(url_for('login'))

@app.route('/subscribe')
@login_required
def subscribe():
    return render_template('subscribe.html', 
                         monthly_plan_id=RAZORPAY_MONTHLY_PLAN_ID,
                         yearly_plan_id=RAZORPAY_YEARLY_PLAN_ID,
                         razorpay_key=config.RAZORPAY_KEY_ID)

@app.route('/create-subscription', methods=['POST'])
@login_required
def create_subscription():
    data = request.get_json()
    plan_id = data.get('plan_id')
    
    try:
        subscription_data = {
            'plan_id': plan_id,
            'total_count': 12 if plan_id == RAZORPAY_YEARLY_PLAN_ID else 1,
            'customer_notify': 1
        }
        
        subscription = razorpay_client.subscription.create(subscription_data)
        
        return jsonify({
            'success': True,
            'subscription_id': subscription['id'],
            'subscription': subscription
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400

@app.route('/verify-subscription', methods=['POST'])
@login_required
def verify_subscription():
    data = request.get_json()
    subscription_id = data.get('subscription_id')
    payment_id = data.get('razorpay_payment_id')
    signature = data.get('razorpay_signature')
    
    try:
        subscription = razorpay_client.subscription.fetch(subscription_id)
        
        if subscription['status'] in ['active', 'authenticated']:
            plan_type = 'yearly' if subscription['plan_id'] == RAZORPAY_YEARLY_PLAN_ID else 'monthly'
            
            current_user.is_subscribed = True
            current_user.subscription_id = subscription_id
            current_user.subscription_status = 'active'
            current_user.subscription_type = plan_type
            current_user.subscription_start = datetime.utcnow()
            
            if plan_type == 'yearly':
                current_user.subscription_end = datetime.utcnow() + timedelta(days=365)
            else:
                current_user.subscription_end = get_month_end()
            
            history = SubscriptionHistory(
                user_id=current_user.id,
                plan_type=plan_type,
                start_date=current_user.subscription_start,
                end_date=current_user.subscription_end,
                status='active'
            )
            db.session.add(history)
            db.session.commit()
            
            return jsonify({'success': True, 'message': 'Subscription activated!'})
        else:
            return jsonify({'success': False, 'error': 'Subscription not active'}), 400
            
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400

@app.route('/cancel-subscription', methods=['POST'])
@login_required
def cancel_subscription():
    if not current_user.subscription_id:
        return jsonify({'success': False, 'error': 'No active subscription'}), 400
    
    try:
        razorpay_client.subscription.cancel(current_user.subscription_id)
        
        current_user.subscription_status = 'cancelled'
        db.session.commit()
        
        return jsonify({'success': True, 'message': 'Subscription cancelled'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400

@app.route('/create-admin', methods=['GET', 'POST'])
def create_admin():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        
        existing_user = User.query.filter_by(email=email).first()
        if existing_user:
            flash('User already exists!', 'error')
            return render_template('create_admin.html')
        
        admin_user = User(
            email=email,
            username=email.split('@')[0],
            password=generate_password_hash(password),
            is_admin=True,
            is_subscribed=True,
            subscription_status='active'
        )
        
        db.session.add(admin_user)
        db.session.commit()
        
        flash('Admin created successfully! You can now login.', 'success')
        return redirect(url_for('login'))
    
    return render_template('create_admin.html')

@app.route('/exchange-connections')
@login_required
@subscription_required
def exchange_connections():
    connections = ExchangeConnection.query.filter_by(user_id=current_user.id).all()
    return render_template('exchange_connections.html', 
                         connections=connections,
                         supported_exchanges=config.SUPPORTED_EXCHANGES)

@app.route('/add-exchange', methods=['POST'])
@login_required
@subscription_required
def add_exchange():
    data = request.get_json()
    exchange_type = data.get('exchange_type')
    api_key = data.get('api_key')
    api_secret = data.get('api_secret')
    connection_name = data.get('connection_name', f"{exchange_type.title()} Account")
    
    if not exchange_type or not api_key or not api_secret:
        return jsonify({'success': False, 'error': 'Missing required fields'}), 400
    
    existing = ExchangeConnection.query.filter_by(
        user_id=current_user.id,
        exchange_type=exchange_type
    ).first()
    
    if existing:
        existing.api_key = api_key
        existing.api_secret = api_secret
        existing.connection_name = connection_name
        existing.is_connected = False
        existing.updated_at = datetime.utcnow()
        connection = existing
    else:
        connection = ExchangeConnection(
            user_id=current_user.id,
            exchange_type=exchange_type,
            api_key=api_key,
            api_secret=api_secret,
            connection_name=connection_name,
            is_connected=False
        )
        db.session.add(connection)
    
    try:
        db.session.commit()
        
        if exchange_type == 'binance':
            try:
                client = logic.get_user_exchange_client(current_user.id)
                if client:
                    account = client.futures_account(recvWindow=10000)
                    if account:
                        connection.is_connected = True
                        connection.last_verified = datetime.utcnow()
                        db.session.commit()
                        
                        return jsonify({
                            'success': True,
                            'message': 'Binance connected successfully!',
                            'connection_id': connection.id
                        })
                
                return jsonify({
                    'success': False,
                    'error': 'Failed to verify connection. Check API keys and permissions.'
                }), 400
                
            except Exception as e:
                error_msg = str(e)
                error_code = None
                
                if 'code' in error_msg:
                    try:
                        import re
                        match = re.search(r'code["\s:=-]+(-?\d+)', error_msg)
                        if match:
                            error_code = int(match.group(1))
                    except:
                        pass
                
                error_response = {'success': False, 'error': error_msg}
                
                if error_code and error_code in config.BINANCE_ERROR_CODES:
                    error_info = config.BINANCE_ERROR_CODES[error_code]
                    error_response.update({
                        'title': error_info['title'],
                        'message': error_info['message'],
                        'error_code': error_code
                    })
                
                return jsonify(error_response), 400
        
        return jsonify({
            'success': True,
            'message': f'{exchange_type.title()} credentials saved',
            'connection_id': connection.id
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/verify-exchange/<int:connection_id>', methods=['POST'])
@login_required
@subscription_required
def verify_exchange(connection_id):
    connection = ExchangeConnection.query.filter_by(
        id=connection_id,
        user_id=current_user.id
    ).first()
    
    if not connection:
        return jsonify({'success': False, 'error': 'Connection not found'}), 404
    
    try:
        if connection.exchange_type == 'binance':
            logic.clear_user_client(current_user.id)
            client = logic.get_user_exchange_client(current_user.id)
            
            if client:
                account = client.futures_account(recvWindow=10000)
                if account:
                    connection.is_connected = True
                    connection.last_verified = datetime.utcnow()
                    db.session.commit()
                    
                    return jsonify({
                        'success': True,
                        'message': 'Connection verified successfully!'
                    })
            
            return jsonify({
                'success': False,
                'error': 'Verification failed'
            }), 400
            
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400

@app.route('/disconnect-exchange/<int:connection_id>', methods=['POST'])
@login_required
@subscription_required
def disconnect_exchange(connection_id):
    connection = ExchangeConnection.query.filter_by(
        id=connection_id,
        user_id=current_user.id
    ).first()
    
    if not connection:
        return jsonify({'success': False, 'error': 'Connection not found'}), 404
    
    try:
        db.session.delete(connection)
        db.session.commit()
        
        logic.clear_user_client(current_user.id)
        
        return jsonify({'success': True, 'message': 'Exchange disconnected'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route("/get_trade_events")
@login_required
@subscription_required
def get_trade_events_api():
    events = logic.get_trade_events(current_user.id)
    return jsonify({"events": events})

@app.route("/get_live_price/<symbol>")
@login_required
def live_price_api(symbol):
    price = logic.get_live_price(symbol, current_user.id)
    return jsonify({"price": price if price else 0})

@app.route("/get_open_positions")
@login_required
@subscription_required
def get_open_positions_api():
    positions = logic.get_open_positions(current_user.id)
    return jsonify({"positions": positions})

@app.route("/get_trade_history")
@login_required
@subscription_required
def get_trade_history_api():
    trades = logic.get_trade_history(current_user.id)
    return jsonify({"trades": trades})

@app.route("/api/wallet")
@login_required
@subscription_required
def get_wallet_api():
    wallet_data = logic.get_wallet_balances(current_user.id)
    
    from models import ExchangeConnection
    connection = ExchangeConnection.query.filter_by(
        user_id=current_user.id, 
        exchange_type='binance', 
        is_connected=True
    ).first()
    
    wallet_data['connection_status'] = {
        'exists': bool(connection),
        'connected': connection.is_connected if connection else False,
        'name': connection.connection_name if connection else None,
        'last_verified': connection.last_verified.isoformat() if connection and connection.last_verified else None
    }
    
    return jsonify(wallet_data)

@app.route("/clear_trade_events", methods=["POST"])
@login_required
@subscription_required
def clear_trade_events_api():
    if "trade_events" in session:
        session["trade_events"] = []
        session.modified = True
    return jsonify({"success": True})

@app.route("/close_position/<symbol>", methods=["POST"])
@login_required
@subscription_required
def close_position_api(symbol):
    result = logic.close_position(symbol, current_user.id)
    return jsonify(result)

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
    result = logic.partial_close_position(symbol, close_percent, close_qty, current_user.id)
    return jsonify(result)

@app.route("/api/trail_sl", methods=["POST"])
@login_required
@subscription_required
def trail_sl_api():
    data = request.get_json()
    symbol = data.get('symbol')
    if not symbol:
        return jsonify({"success": False, "message": "Symbol required"}), 400
    result = logic.trail_stop_loss(symbol, current_user.id)
    return jsonify(result)

@app.route("/api/live_pnl/<symbol>")
@login_required
@subscription_required
def live_pnl_api(symbol):
    result = logic.get_live_pnl(symbol, current_user.id)
    return jsonify(result)

@app.route("/api/today_stats")
@login_required
@subscription_required
def today_stats_api():
    stats = logic.get_today_stats(current_user.id)
    return jsonify(stats)

@app.route("/update_sl", methods=["POST"])
@login_required
@subscription_required
def update_sl_api():
    return trail_sl_api()

@app.route("/download_trades")
@login_required
@subscription_required
def download_trades():
    trades = logic.get_trade_history(current_user.id)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Time (UTC)', 'Symbol', 'Side', 'Quantity', 'Price', 'Realized PnL', 'Commission', 'Order ID'])
    for trade in trades:
        writer.writerow([trade.get('time', ''), trade.get('symbol', ''), trade.get('side', ''), 
                        trade.get('qty', ''), trade.get('price', ''), trade.get('realized_pnl', ''), 
                        trade.get('commission', ''), trade.get('order_id', '')])
    output.seek(0)
    return Response(output.getvalue(), mimetype='text/csv', 
                   headers={'Content-Disposition': f'attachment; filename=trade_history_{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}.csv'})

@app.route("/index", methods=["GET", "POST"])
@login_required
@subscription_required
def index():
    logic.initialize_session()
    symbols = logic.get_all_exchange_symbols(current_user.id)
    
    balance_data = logic.get_live_balance(current_user.id)
    balance = 0.0
    margin_used = 0.0

    if balance_data and isinstance(balance_data, tuple):
        if len(balance_data) >= 2:
            balance = float(balance_data[0] or 0.0)
            margin_used = float(balance_data[1] or 0.0)
        elif isinstance(balance_data[0], tuple) and len(balance_data[0]) >= 2:
            balance = float(balance_data[0][0] or 0.0)
            margin_used = float(balance_data[0][1] or 0.0)
    
    unutilized = max(balance - margin_used, 0.0)
    
    wallet_response = logic.get_wallet_balances(current_user.id)
    wallet_debug = {
        'success': wallet_response.get('success', False),
        'error': wallet_response.get('error', ''),
        'debug_info': wallet_response.get('debug_info', {}),
        'total_assets': wallet_response.get('total_assets', 0),
        'unutilized': unutilized,
        'needs_connection': not wallet_response.get('success') and 'client' in str(wallet_response.get('error', '')).lower()
    }
    
    today_stats = logic.get_today_stats(current_user.id)

    selected_symbol = request.form.get("symbol", "BTCUSDT")
    side = request.form.get("side", "LONG")
    order_type = request.form.get("order_type", "MARKET")
    margin_mode = request.form.get("margin_mode", "ISOLATED")

    entry = float(request.form.get("entry") or logic.get_live_price(selected_symbol, current_user.id) or 0)
    sl_type = request.form.get("sl_type", "SL % Movement")
    sl_val = float(request.form.get("sl_value") or 0)

    tp1_mode = request.form.get("tp1_mode", "TP1 Price")
    raw_tp1 = float(request.form.get("tp1") or 0)
    tp1_pct = float(request.form.get("tp1_pct") or 0)
    tp2 = float(request.form.get("tp2") or 0)

    if tp1_mode == "TP1 % Movement" and raw_tp1 > 0:
        tp1 = entry * (1 + (raw_tp1 / 100)) if side == "LONG" else entry * (1 - (raw_tp1 / 100))
    else:
        tp1 = raw_tp1

    # Calculate position sizing with symbol parameter for max leverage fetching
    sizing = logic.calculate_position_sizing(
        unutilized, 
        entry, 
        sl_type, 
        sl_val, 
        side, 
        user_id=current_user.id, 
        symbol=selected_symbol
    )
    
    trade_status = None
    
    # ENHANCED: Handle trade execution with all new validations
    if request.method == "POST" and request.form.get("place_order"):
        # Check if SL is set (MANDATORY)
        if not sl_val or sl_val <= 0:
            trade_status = {
                "success": False,
                "message": "🚨 STOP LOSS IS MANDATORY! Cannot execute trade without SL."
            }
        # Check if sizing calculation was successful
        elif not sizing.get('can_trade'):
            trade_status = {
                "success": False,
                "message": sizing.get('error', 'Invalid trade parameters')
            }
        # Check daily limits
        else:
            can_trade, limit_msg = logic.can_place_trade(selected_symbol, current_user.id)
            if not can_trade:
                trade_status = {
                    "success": False,
                    "message": limit_msg
                }
            else:
                # Execute the trade
                result = logic.place_trade_with_1pct_risk(
                    symbol=selected_symbol,
                    side=side,
                    entry=entry,
                    sl_type=sl_type,
                    sl_value=sl_val,
                    tp1=tp1,
                    tp1_pct=tp1_pct,
                    tp2=tp2,
                    order_type=order_type,
                    margin_mode=margin_mode,
                    user_id=current_user.id
                )
                trade_status = result
                
                # Recalculate stats after trade
                if result.get('success'):
                    today_stats = logic.get_today_stats(current_user.id)

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
        default_tp1_value=raw_tp1,
        default_tp1_mode=tp1_mode,
        today_stats=today_stats,
        wallet_debug=wallet_debug
    )

with app.app_context():
    db.create_all()

@app.errorhandler(404)
def not_found_error(error):
    return render_template('home.html'), 404

@app.errorhandler(500)
def internal_error(error):
    db.session.rollback()  
    return render_template('home.html'), 500

@app.errorhandler(Exception)
def handle_exception(error):
    if hasattr(error, 'code') and 400 <= error.code < 600:
        return error
    import traceback
    traceback.print_exc()
    return render_template('home.html'), 500

@app.route('/debug-wallet')
@login_required
def debug_wallet():
    user_id = request.args.get('user_id')
    if not user_id or not user_id.isdigit():
        return "❌ Provide ?user_id=1 (your user ID)", 400
    
    user_id = int(user_id)
    live_balance_data = logic.get_live_balance(user_id)
    wallet_data = logic.get_wallet_balances(user_id)
    
    from models import ExchangeConnection
    connection = ExchangeConnection.query.filter_by(
        user_id=user_id, exchange_type='binance'
    ).first()
    
    return render_template('debug.html', 
                         user_id=user_id,
                         live_balance=live_balance_data,
                         wallet_data=wallet_data,
                         connection=connection)

@app.route('/test-binance')
@login_required
@subscription_required
def test_binance():
    try:
        import logic
        client = logic.get_client(current_user.id)
        balance, margin = logic.get_live_balance(current_user.id)
        btc_price = logic.get_live_price('BTCUSDT', current_user.id)
        symbols = logic.get_all_exchange_symbols(current_user.id)[:5]  
        
        proxy_status = 'Configured' if getattr(config, 'PROXY_URL', None) else 'Not set'
        
        test_result = {
            'status': 'success',
            'client_available': client is not None,
            'balance': balance,
            'margin_used': margin,
            'btc_price': btc_price,
            'symbol_count': len(symbols),
            'sample_symbols': symbols,
            'proxy_status': proxy_status,
            'message': '✅ Binance connection OK!' if client else '⚠️ No connection - add your exchange keys'
        }
        return jsonify(test_result)
    except Exception as e:
        return jsonify({
            'status': 'error',
            'error': str(e),
            'message': '❌ Test failed - check VPN/proxy if geo-restricted'
        }), 500

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000, debug=True)