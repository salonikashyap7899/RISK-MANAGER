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
        
        # ✅ PRIORITY 1: Admin bypass (uses is_admin field from models.py)
        if getattr(current_user, 'is_admin', False):
            print(f"✅ Admin {current_user.username} ({current_user.id}) bypassing subscription check")
            return f(*args, **kwargs)
        
        # ✅ PRIORITY 2: Hardcoded admin emails (fallback)
        ADMIN_EMAILS = ['admin@mindriskcontrol.com', 'test@test.com']
        if current_user.email.lower() in [email.lower() for email in ADMIN_EMAILS]:
            print(f"✅ Admin email {current_user.email} bypassing subscription check")
            return f(*args, **kwargs)
        
        # FIXED: More robust subscription check for regular users
        now = datetime.utcnow()
        
        # Check if user has an active subscription
        if not current_user.is_subscribed:
            flash("Please subscribe to access the trading dashboard.", "warning")
            return redirect(url_for('subscribe'))
        
        # Check if subscription has expired - only if subscription_end is set
        if current_user.subscription_end:
            if now > current_user.subscription_end:
                # Subscription has expired
                current_user.is_subscribed = False
                current_user.subscription_status = 'expired'
                db.session.commit()
                flash("Your subscription has expired. Please renew to access the dashboard.", "warning")
                return redirect(url_for('subscribe'))
        else:
            # If subscription_end is not set but is_subscribed is True, 
            # treat as active for monthly subscribers
            pass
            
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

        if User.query.filter_by(email=email).first():
            flash('Email already registered. Please log in or use another email.', 'error')
            return render_template('register.html')

        if User.query.filter_by(username=username).first():
            flash('Username already taken. Choose a different username.', 'error')
            return render_template('register.html')

        hashed_pw = generate_password_hash(password)
        new_user = User(
            username=username,
            email=email,
            password=hashed_pw
        )
        try:
            db.session.add(new_user)
            db.session.commit()
            flash('Registration successful. Please log in.', 'success')
            return redirect(url_for('login'))
        except Exception:
            db.session.rollback()
            flash('An error occurred during registration. Please try again.', 'error')
            return render_template('register.html')

    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()  # Convert to lowercase
        password = request.form.get('password')

        user = User.query.filter_by(email=email).first()

        if not user or not check_password_hash(user.password, password):
            flash("Invalid email or password", "error")
            return render_template('login.html')

        # FIXED: Allow multiple device login - removed restrictive active_session check
        # Users can now log in from any device without being forced to logout first
        
        # Create session
        login_user(user, remember=True)  # Added remember=True for persistent login
        session_id = str(uuid.uuid4())
        session['session_id'] = session_id
        session.permanent = True  # Make session permanent
        
        # Clear any old active_session to allow fresh login
        # Don't block login - just update the session
        user.active_session = session_id

        db.session.commit()
        
        
        # FIXED: More robust subscription check on login
        # First check if user has subscription flag
        is_admin = getattr(user, 'is_admin', False) or user.email.lower() in ['admin@mindriskcontrol.com', 'test@test.com']
    
        if not is_admin:
        # First check if user has subscription flag
         if not user.is_subscribed:
            flash("Please subscribe to access the trading dashboard.", "warning")
            return redirect(url_for('subscribe'))
        
        # Check if subscription has expired
        if user.subscription_end:
            if datetime.utcnow() > user.subscription_end:
                user.is_subscribed = False
                user.subscription_status = 'expired'
                db.session.commit()
                flash("Your subscription has expired. Please renew to access the dashboard.", "warning")
                return redirect(url_for('subscribe'))
        
        # Subscription is valid
        return redirect(url_for('index'))

    return render_template('login.html')

@app.route('/login/google')
def google_login():
    return google.authorize_redirect(url_for('google_authorize', _external=True))

@app.route('/authorize/google')
def google_authorize():
    token = google.authorize_access_token()
    user_info = token.get('userinfo')
    
    # Convert email to lowercase to match registration format
    email_lower = user_info['email'].lower()
    user = User.query.filter_by(email=email_lower).first()
    
    if not user:
        user = User(
            username=user_info['name'], 
            email=email_lower, 
            google_id=user_info['sub']
        )
        db.session.add(user)
        db.session.commit()
    
    # FIXED: Allow multiple device login - removed restrictive active_session check
    login_user(user, remember=True)
    
    session_id = str(uuid.uuid4())
    session['session_id'] = session_id
    session.permanent = True
    user.active_session = session_id
    
    db.session.commit()
    
    # FIXED: More robust subscription check on Google login
    # First check if user has subscription flag
    if not user.is_subscribed:
        flash("Please subscribe to access the trading dashboard.", "warning")
        return redirect(url_for('subscribe'))
    
    # Check if subscription has expired - only if subscription_end is set
    if user.subscription_end:
        if datetime.utcnow() > user.subscription_end:
            # Subscription has expired
            user.is_subscribed = False
            user.subscription_status = 'expired'
            db.session.commit()
            flash("Your subscription has expired. Please renew to access the dashboard.", "warning")
            return redirect(url_for('subscribe'))
    
    # Subscription is valid
    return redirect(url_for('index'))

@app.route('/logout')
@login_required
def logout():
    current_user.active_session = None
    db.session.commit()
    logout_user()
    session.clear()
    return redirect(url_for('login'))

# Debug route to clear stuck sessions - use in browser: /clear-session
@app.route('/clear-session')
def clear_session_debug():
    """Debug route to clear all user sessions - for stuck users"""
    from models import User
    users = User.query.all()
    for user in users:
        user.active_session = None
    db.session.commit()
    return "All user sessions cleared! <a href='/login'>Go to Login</a>"

# Debug route to create test admin user - use in browser: /create-admin
@app.route('/create-admin')
def create_admin_debug():
    """Debug route to create a test admin user"""
    from models import User
    from werkzeug.security import generate_password_hash
    
    # Check if admin already exists
    admin = User.query.filter_by(email='test@test.com').first()
    if admin:
        return "Admin user already exists! <br>Email: test@test.com <br>Password: Test@123 <br><a href='/login'>Go to Login</a>"
    
    # Create admin user
    hashed_pw = generate_password_hash('Test@123')
    admin = User(
        username='Admin',
        email='test@test.com',
        password=hashed_pw,
        is_subscribed=True,
        subscription_status='active',
        subscription_type='pro'
    )
    db.session.add(admin)
    db.session.commit()
    
    return "Admin user created successfully! <br>Email: test@test.com <br>Password: Test@123 <br><a href='/login'>Go to Login</a>"

@app.route('/subscribe')
@login_required
def subscribe():
    return render_template('subscribe.html', key_id=config.RAZORPAY_KEY_ID, user=current_user)

@app.route('/create-subscription', methods=['POST'])
@login_required
def create_subscription():
    try:
        data = request.get_json()
        plan_type = data.get("plan_type")

        if not plan_type:
            return jsonify({"success": False, "error": "plan_type is required"}), 400

        if plan_type == "monthly":
            plan_id = RAZORPAY_MONTHLY_PLAN_ID
        elif plan_type == "yearly":
            plan_id = RAZORPAY_YEARLY_PLAN_ID
        else:
            return jsonify({"success": False, "error": "Invalid plan type"}), 400

        subscription_data = {
            "plan_id": plan_id,
            "total_count": 12 if plan_type == "monthly" else 1,
            "quantity": 1,
            "customer_notify": 1,
            "notes": {
                "user_id": current_user.id,
                "email": current_user.email
            }
        }
        
        subscription = razorpay_client.subscription.create(subscription_data)
        session["pending_plan_type"] = plan_type
        
        return jsonify({
            "success": True,
            "subscription_id": subscription["id"]
        })

    except Exception as e:
        print(f"Error creating subscription: {e}")
        return jsonify({"success": False, "error": str(e)}), 400

@app.route('/verify-subscription', methods=['POST'])
@login_required
def verify_subscription():
    try:
        data = request.get_json()

        razorpay_client.utility.verify_subscription_payment_signature({
            "razorpay_payment_id": data.get("razorpay_payment_id"),
            "razorpay_subscription_id": data.get("razorpay_subscription_id"),
            "razorpay_signature": data.get("razorpay_signature")
        })

        plan_type = session.pop("pending_plan_type", "monthly")
        duration_days = 365 if plan_type == "yearly" else 30

        current_user.is_subscribed = True
        current_user.subscription_id = data.get("razorpay_subscription_id")
        current_user.subscription_status = "active"
        current_user.subscription_type = plan_type
        current_user.subscription_start = datetime.utcnow()
        current_user.subscription_end = datetime.utcnow() + timedelta(days=duration_days)

        # FIXED: Create permanent subscription history record
        subscription_history = SubscriptionHistory(
            user_id=current_user.id,
            plan_type=plan_type,
            start_date=current_user.subscription_start,
            end_date=current_user.subscription_end,
            status="active"
        )
        db.session.add(subscription_history)
        
        db.session.commit()

        return jsonify({
            "success": True,
            "plan": plan_type,
            "valid_till": current_user.subscription_end.strftime("%Y-%m-%d")
        })

    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)}), 400

@app.route('/cancel-subscription', methods=['POST'])
@login_required
def cancel_subscription():
    try:
        if current_user.subscription_id:
            try:
                razorpay_client.subscription.cancel(current_user.subscription_id)
            except Exception as e:
                print(f"Razorpay cancellation error: {e}")
        
        current_user.is_subscribed = False
        current_user.subscription_status = "cancelled"
        current_user.subscription_end = datetime.utcnow()
        
        db.session.commit()
        
        return jsonify({
            "success": True,
            "message": "Subscription cancelled successfully"
        })
    
    except Exception as e:
        db.session.rollback()
        return jsonify({
            "success": False,
            "error": str(e)
        }), 400

@app.route('/check-subscription')
@login_required
def check_subscription():
    if current_user.is_subscribed:
        if current_user.subscription_end and current_user.subscription_end > datetime.utcnow():
            days_left = (current_user.subscription_end - datetime.utcnow()).days
            return jsonify({
                'subscribed': True,
                'status': current_user.subscription_status,
                'type': current_user.subscription_type,
                'start_date': current_user.subscription_start.strftime('%Y-%m-%d') if current_user.subscription_start else None,
                'end_date': current_user.subscription_end.strftime('%Y-%m-%d'),
                'days_left': days_left
            })
        else:
            current_user.subscription_status = 'expired'
            current_user.is_subscribed = False
            db.session.commit()
            return jsonify({'subscribed': False, 'status': 'expired'})
    
    return jsonify({'subscribed': False, 'status': 'inactive'})


# EXCHANGE CONNECTION ROUTES

@app.route('/exchange-connections')
@login_required
def exchange_connections():
    connections = ExchangeConnection.query.filter_by(user_id=current_user.id).all()
    return render_template(
        'exchange_connections.html',
        connections=connections,
        supported_exchanges=config.SUPPORTED_EXCHANGES
    )


@app.route('/add-exchange', methods=['POST'])
@login_required
@subscription_required
def add_exchange():
    try:
        data = request.get_json()
        exchange_type = data.get('exchange_type')
        api_key = data.get('api_key', '').strip()
        api_secret = data.get('api_secret', '').strip()
        connection_name = data.get('connection_name', '').strip()
        
        if exchange_type not in config.SUPPORTED_EXCHANGES:
            return jsonify({'success': False, 'error': 'Invalid exchange type'}), 400
        
        required_fields = config.SUPPORTED_EXCHANGES[exchange_type]['api_required']
        if not api_key or not api_secret:
            return jsonify({'success': False, 'error': f'Missing required fields: {", ".join(required_fields)}'}), 400
        
        existing = ExchangeConnection.query.filter_by(
            user_id=current_user.id,
            exchange_type=exchange_type,
            is_connected=True
        ).first()
        
        if existing:
            return jsonify({'success': False, 'error': f'You already have a {exchange_type} connection. Please disconnect it first.'}), 400
        
        connection = ExchangeConnection(
            user_id=current_user.id,
            exchange_type=exchange_type,
            api_key=api_key,
            api_secret=api_secret,
            connection_name=connection_name or f"My {exchange_type} Account",
            is_connected=False
        )
        
        db.session.add(connection)
        db.session.commit()
        
        if exchange_type == 'binance':
            from binance.client import Client
            from binance.exceptions import BinanceAPIException
            
            # Basic key validation (now optional - comment shows expected format)
            # if not (api_key.startswith(('vmPU', 'uD')) and len(api_key) > 20):
            #     db.session.delete(connection)
            #     db.session.commit()
            #     return jsonify({
            #         'success': False, 
            #         'error': 'Invalid API key format. Binance keys start with vmPU... or uD... (64+ chars)'
            #     }), 400

            try:
                client = Client(api_key, api_secret, {'timeout': 20})
                client.futures_account(recvWindow=60000)
                connection.is_connected = True
                connection.last_verified = datetime.utcnow()
                db.session.commit()
                
                logic.clear_user_client(current_user.id)
                
                return jsonify({'success': True, 'message': f'{exchange_type} connected successfully!'})
            except BinanceAPIException as e:
                error_info = config.BINANCE_ERROR_CODES.get(e.code)
                db.session.delete(connection)
                db.session.commit()
                return jsonify({
                    'success': False,
                    'error_code': getattr(e, 'code', None),
                    'title': error_info['title'] if error_info else f'Binance Error {e.code}',
                    'message': error_info['message'] if error_info else str(e),
                    'raw_error': str(e)
                }), 400
            except Exception as e:
                db.session.delete(connection)
                db.session.commit()
                return jsonify({'success': False, 'error': f'Unexpected error: {str(e)}'}), 400
        
        connection.is_connected = True
        connection.last_verified = datetime.utcnow()
        db.session.commit()
        
        return jsonify({'success': True, 'message': f'{exchange_type} added successfully!'})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 400


@app.route('/verify-exchange/<int:connection_id>', methods=['POST'])
@login_required
@subscription_required
def verify_exchange(connection_id):
    connection = ExchangeConnection.query.get_or_404(connection_id)
    
    if connection.user_id != current_user.id:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    
    try:
        if connection.exchange_type == 'binance':
            from binance.client import Client
            from binance.exceptions import BinanceAPIException
            
            try:
                client = Client(connection.api_key, connection.api_secret, {'timeout': 20})
                client.futures_account(recvWindow=60000)
                
                connection.is_connected = True
                connection.last_verified = datetime.utcnow()
                db.session.commit()
                
                logic.clear_user_client(current_user.id)
                
                return jsonify({'success': True, 'message': 'Connection verified successfully!'})
            except BinanceAPIException as e:
                error_info = config.BINANCE_ERROR_CODES.get(e.code)
                connection.is_connected = False
                db.session.commit()
                return jsonify({
                    'success': False,
                    'error_code': getattr(e, 'code', None),
                    'title': error_info['title'] if error_info else f'Binance Error {e.code}',
                    'message': error_info['message'] if error_info else str(e),
                    'raw_error': str(e)
                }), 400
            except Exception as e:
                connection.is_connected = False
                db.session.commit()
                return jsonify({'success': False, 'error': f'Unexpected error: {str(e)}'}), 400
        
        return jsonify({'success': True, 'message': 'Connection is active'})
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


@app.route('/disconnect-exchange/<int:connection_id>', methods=['POST'])
@login_required
@subscription_required
def disconnect_exchange(connection_id):
    connection = ExchangeConnection.query.get_or_404(connection_id)
    
    if connection.user_id != current_user.id:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    
    try:
        logic.clear_user_client(current_user.id)
        
        db.session.delete(connection)
        db.session.commit()
        
        return jsonify({'success': True, 'message': 'Exchange disconnected successfully'})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 400


@app.route('/get-exchange-status')
@login_required
@subscription_required
def get_exchange_status():
    connections = ExchangeConnection.query.filter_by(
        user_id=current_user.id,
        is_connected=True
    ).all()
    
    return jsonify({
        'success': True,
        'connections': [{
            'id': c.id,
            'exchange_type': c.exchange_type,
            'connection_name': c.connection_name,
            'last_verified': c.last_verified.strftime('%Y-%m-%d %H:%M') if c.last_verified else None
        } for c in connections]
    })


# TRADING ROUTES - All use user's connected exchange via user_id

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
    """FIXED: Dedicated wallet endpoint with diagnostics"""
    wallet_data = logic.get_wallet_balances(current_user.id)
    
    # Add connection status summary
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
    
    print(f"🌐 /api/wallet response: success={wallet_data.get('success')}, assets={wallet_data.get('total_assets',0)}")
    return jsonify(wallet_data)

@app.route("/get_today_stats")
@login_required
@subscription_required
def get_today_stats_api():
    stats = logic.get_today_stats()
    return jsonify(stats)

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

@app.route("/update_sl", methods=["POST"])
@login_required
@subscription_required
def update_sl_api():
    data = request.get_json()
    symbol = data.get('symbol')
    new_sl_percent = float(data.get('new_sl_percent', 0))
    if not symbol:
        return jsonify({"success": False, "message": "Symbol required"})
    result = logic.update_stop_loss(symbol, new_sl_percent, current_user.id)
    return jsonify(result)

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
    
    # FIXED: Enhanced diagnostics + wallet status
    balance_data = logic.get_live_balance(current_user.id)
    balance = 0.0
    margin_used = 0.0
    wallet_debug = {}

    if balance_data and isinstance(balance_data, tuple):
        inner_tuple = balance_data[0]
        if isinstance(inner_tuple, tuple) and len(inner_tuple) >= 2:
            balance = float(inner_tuple[0] or 0.0)
            margin_used = float(inner_tuple[1] or 0.0)
    
    unutilized = max(balance - margin_used, 0.0)
    
    # Get wallet details for debug
    wallet_response = logic.get_wallet_balances(current_user.id)
    wallet_debug = {
        'success': wallet_response.get('success', False),
        'error': wallet_response.get('error', ''),
        'debug_info': wallet_response.get('debug_info', {}),
        'total_assets': wallet_response.get('total_assets', 0),
        'unutilized': unutilized,
        'needs_connection': not wallet_response.get('success') and 'client' in str(wallet_response.get('error', '')).lower()
    }
    
    print(f"📊 /index wallet_debug: {wallet_debug}")
    # -------------------------------------

    selected_symbol = request.form.get("symbol", "BTCUSDT")
    side = request.form.get("side", "LONG")
    order_type = request.form.get("order_type", "MARKET")
    margin_mode = request.form.get("margin_mode", "ISOLATED")

    entry = float(request.form.get("entry") or logic.get_live_price(selected_symbol, current_user.id) or 0)
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
        today_stats=today_stats,
        wallet_debug=wallet_debug
    )

with app.app_context():
    db.create_all()

# ============================================
# ERROR HANDLERS - Add these after app creation
# ============================================

@app.errorhandler(404)
def not_found_error(error):
    return render_template('home.html'), 404

@app.errorhandler(500)
def internal_error(error):
    db.session.rollback()  # Rollback any failed database transactions
    print(f"❌ Internal Server Error: {error}")
    return render_template('home.html'), 500

@app.errorhandler(Exception)
def handle_exception(error):
    # Pass through HTTP errors
    if hasattr(error, 'code') and 400 <= error.code < 600:
        return error
    
    # Handle all other exceptions
    print(f"❌ Unhandled Exception: {error}")
    import traceback
    traceback.print_exc()
    return render_template('home.html'), 500

# Debug route to check server status
@app.route('/debug-wallet')
@login_required
def debug_wallet():
    """DEBUG: Test wallet for specific user_id"""
    user_id = request.args.get('user_id')
    if not user_id or not user_id.isdigit():
        return "❌ Provide ?user_id=1 (your user ID)", 400
    
    user_id = int(user_id)
    
    # Test live balance
    live_balance_data = logic.get_live_balance(user_id)
    
    # Test full wallet
    wallet_data = logic.get_wallet_balances(user_id)
    
    # Check connection
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
    """Test Binance connectivity for logged-in user"""
    try:
        import logic
        client = logic.get_client(current_user.id)
        balance, margin = logic.get_live_balance(current_user.id)
        btc_price = logic.get_live_price('BTCUSDT', current_user.id)
        symbols = logic.get_all_exchange_symbols(current_user.id)[:5]  # First 5
        
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
