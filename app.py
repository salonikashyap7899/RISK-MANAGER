from flask import Flask, render_template, request, session, jsonify, redirect, url_for, Response, flash
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from authlib.integrations.flask_client import OAuth
from dotenv import load_dotenv
from datetime import datetime, timedelta
from functools import wraps
from models import db, User, ExchangeConnection, SubscriptionHistory, TradeDailyStats, TradeLog
from flask import Flask, render_template, redirect, url_for, flash, jsonify, request, session, make_response
from logic import select_symbol
import logic
import config
import os
import csv
import io
import uuid
import razorpay
import time
import re

# Load environment variables
load_dotenv()

app = Flask(__name__)

# Secure secret key - use environment variable or generate one
app.secret_key = os.getenv('SESSION_SECRET') or os.getenv('SECRET_KEY', 'dev-fallback-change-in-production')

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

DISPOSABLE_DOMAINS = {
    'mailinator.com', 'tempmail.com', 'throwaway.email', 'guerrillamail.com',
    'yopmail.com', 'trashmail.com', 'sharklasers.com', 'maildrop.cc',
    'fakeinbox.com', 'tempinbox.com', 'trashmail.me', 'trashmail.net',
    'temp-mail.org', 'mohmal.com', 'discard.email', 'spamgourmet.com',
    'mailexp.com', 'throwam.com', 'spam4.me', 'dispostable.com',
    'mailnull.com', 'getairmail.com', 'filzmail.com', 'trbvm.com',
    'grr.la', 'guerrillamail.info', 'guerrillamail.biz', 'guerrillamail.de',
    'guerrillamail.net', 'guerrillamail.org', 'cuvox.de', 'dayrep.com',
    'einrot.com', 'fleckens.hu', 'superrito.com', 'teleworm.us',
    'rhyta.com', 'armyspy.com', 'guam.net', 'inoutmail.eu',
    'spamgourmet.net', 'spamgourmet.org', 'spamgourmet.com', 'mailnesia.com',
}

def validate_email_address(email):
    pattern = r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$'
    if not re.match(pattern, email):
        return False, 'Please enter a valid email address.'
    domain = email.split('@')[1].lower()
    if domain in DISPOSABLE_DOMAINS:
        return False, 'Disposable or temporary email addresses are not allowed.'
    return True, ''

def validate_password_strength(password):
    if len(password) < 8:
        return False, 'Password must be at least 8 characters long.'
    if not re.search(r'[A-Z]', password):
        return False, 'Password must contain at least one uppercase letter (A-Z).'
    if not re.search(r'[a-z]', password):
        return False, 'Password must contain at least one lowercase letter (a-z).'
    if not re.search(r'[0-9]', password):
        return False, 'Password must contain at least one number (0-9).'
    if not re.search(r'[!@#$%^&*()\-_=+\[\]{}|;:,.<>?/]', password):
        return False, 'Password must contain at least one special character (!@#$%^&* etc).'
    return True, ''

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
        
        # Always require subscription_end to be set for regular users
        if not current_user.subscription_end:
            flash("Your subscription details are incomplete. Please subscribe to access the dashboard.", "warning")
            return redirect(url_for('subscribe'))

        # Block access the moment subscription_end has passed
        if now >= current_user.subscription_end:
            current_user.is_subscribed = False
            current_user.subscription_status = 'expired'
            db.session.commit()
            flash("Your subscription has expired. Please renew to continue accessing the dashboard.", "warning")
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
    if request.method == 'GET':
        return redirect(url_for('home'))

    email = (request.form.get('email') or '').strip().lower()
    username = (request.form.get('username') or '').strip()
    password = request.form.get('password') or ''
    confirm_password = request.form.get('confirm_password') or ''

    def ajax_error(msg, code=400):
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'success': False, 'message': msg}), code
        flash(msg, 'error')
        return redirect(url_for('home'))

    # Validate email format and block disposable domains
    email_ok, email_err = validate_email_address(email)
    if not email_ok:
        return ajax_error(email_err)

    # Enforce strong password
    pw_ok, pw_err = validate_password_strength(password)
    if not pw_ok:
        return ajax_error(pw_err)

    # Check passwords match
    if password != confirm_password:
        return ajax_error('Passwords do not match.')

    if User.query.filter_by(email=email).first():
        return ajax_error('Email already registered. Please log in or use another email.')

    if User.query.filter_by(username=username).first():
        return ajax_error('Username already taken. Choose a different username.')

    hashed_pw = generate_password_hash(password)
    new_user = User(username=username, email=email, password=hashed_pw)
    try:
        db.session.add(new_user)
        db.session.commit()
        msg = 'Registration successful! Please log in.'
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'success': True, 'message': msg}), 200
        flash(msg, 'success')
        return redirect(url_for('home'))
    except Exception:
        db.session.rollback()
        return ajax_error('An error occurred during registration. Please try again.', 500)

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

        # Allow multiple device login
        login_user(user, remember=True)
        session_id = str(uuid.uuid4())
        session['session_id'] = session_id
        session.permanent = True
        user.active_session = session_id
        db.session.commit()

        # Check if admin
        is_admin = getattr(user, 'is_admin', False) or user.email.lower() in ['admin@mindriskcontrol.com', 'test@test.com']

        if not is_admin:
            # Check if subscription has expired
            if user.subscription_end and datetime.utcnow() > user.subscription_end:
                user.is_subscribed = False
                user.subscription_status = 'expired'
                db.session.commit()

            if not user.is_subscribed:
                # Still log them in — redirect to subscribe page
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return jsonify({'success': True, 'redirect': url_for('subscribe')}), 200
                return redirect(url_for('subscribe'))

        # Login successful
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'success': True, 'redirect': url_for('index')}), 200
        return redirect(url_for('index'))

    return redirect(url_for('home'))

@app.route('/login/google')
@app.route('/google-login')
def google_login():
    redirect_uri = url_for('google_authorize', _external=True)
    return google.authorize_redirect(redirect_uri)


@app.route('/login/google/callback')
def google_authorize():
    try:
        token = google.authorize_access_token()
        user_info = token.get('userinfo')
        if not user_info:
            user_info = google.get('userinfo').json()

        email = (user_info.get('email') or '').strip().lower()
        google_id = user_info.get('sub')
        display_name = user_info.get('name', email.split('@')[0])

        if not email or not google_id:
            flash("Google login failed: could not retrieve account info.", "error")
            return redirect(url_for('login'))

        # Find existing user by google_id first, then by email
        user = User.query.filter_by(google_id=google_id).first()
        if not user:
            user = User.query.filter_by(email=email).first()
            if user:
                user.google_id = google_id
            else:
                # Create a unique username from display name
                base_username = display_name.replace(' ', '_').lower()
                username = base_username
                counter = 1
                while User.query.filter_by(username=username).first():
                    username = f"{base_username}{counter}"
                    counter += 1

                user = User(
                    username=username,
                    email=email,
                    google_id=google_id,
                    password=None
                )
                db.session.add(user)
            db.session.commit()

        login_user(user, remember=True)
        session_id = str(uuid.uuid4())
        session['session_id'] = session_id
        session.permanent = True
        user.active_session = session_id
        db.session.commit()

        is_admin = getattr(user, 'is_admin', False) or user.email.lower() in ['admin@mindriskcontrol.com', 'test@test.com']

        if not is_admin:
            if user.subscription_end and datetime.utcnow() > user.subscription_end:
                user.is_subscribed = False
                user.subscription_status = 'expired'
                db.session.commit()
            if not user.is_subscribed:
                return redirect(url_for('subscribe'))

        return redirect(url_for('index'))

    except Exception as e:
        flash(f"Google login error: {str(e)}", "error")
        return redirect(url_for('login'))


@app.route("/api/select_symbol", methods=["POST"])
@login_required
def api_select_symbol():
    # Prefer Flask-Login user, fall back to session user_id
    user_id = current_user.id if current_user.is_authenticated else session.get("user_id")
    data = request.get_json(silent=True) or {}
    symbol = (data.get("symbol") or "").upper().strip()

    if not user_id or not symbol:
        return jsonify({"error": "missing user or symbol"}), 400

    try:
        payload = select_symbol(user_id, symbol)
        return jsonify(payload)
    except Exception as e:
        print(f"❌ Error in api_select_symbol: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/get_live_price/<symbol>")
@login_required
def get_live_price_route(symbol):
    """API endpoint to get live price for a symbol"""
    price = logic.get_live_price(symbol, current_user.id)
    return jsonify({"price": price})

@app.route("/api/coin-details/<symbol>")
@login_required
def api_coin_details(symbol):
    """
    Returns live market data, recommendations, and current position details
    for a specific symbol.
    """
    symbol = symbol.upper().strip()
    sl_type = request.args.get('sl_type', 'SL % Movement')
    sl_value = float(request.args.get('sl_value', 1.5))
    side = request.args.get('side', 'LONG')
    
    try:
        # 1. Get live price
        current_price = logic.get_live_price(symbol, current_user.id)
        
        # 2. Get exchange limits (max leverage)
        max_lev = logic.get_max_leverage(symbol, current_user.id)
        
        # 3. Get position sizing recommendation based on current UI inputs
        # (This uses the same logic as the main execute form)
        balance_data = logic.get_live_balance(current_user.id)
        balance = 0.0
        margin_used = 0.0
        if balance_data and isinstance(balance_data, tuple):
            inner_tuple = balance_data[0]
            if isinstance(inner_tuple, tuple) and len(inner_tuple) >= 2:
                balance = float(inner_tuple[0] or 0.0)
                margin_used = float(inner_tuple[1] or 0.0)
        
        unutilized = max(balance - margin_used, 0.0)
        sizing = logic.calculate_position_sizing(unutilized, current_price, sl_type, sl_value, side, user_id=current_user.id, symbol=symbol)
        
        # 4. Get current open position if any
        all_pos = logic.get_open_positions(current_user.id)
        current_pos = next((p for p in all_pos if p['symbol'] == symbol), None)
        
        # 5. Build calculation breakdown for transparency
        sl_percent = sizing.get('sl_percent', sl_value)
        
        # Add calculation breakdown for ROI/PnL
        calculation_breakdown = {
            "roi": {
                "base_percent": 0.0,
                "leverage": sizing.get('suggested_leverage', 1),
                "calculated_percent": 0.0
            }
        }
        
        if current_pos:
            entry = current_pos.get('entry_price', 0)
            mark = current_pos.get('mark_price', 0)
            if entry > 0:
                base_move = ((mark - entry) / entry * 100) if current_pos['side'] == 'LONG' else ((entry - mark) / entry * 100)
                calculation_breakdown["roi"]["base_percent"] = base_move
                calculation_breakdown["roi"]["calculated_percent"] = base_move * current_pos.get('leverage', 1)

        return jsonify({
            "success": True,
            "symbol": symbol,
            "current_price": current_price,
            "max_leverage": max_lev,
            "suggested_leverage": sizing.get('suggested_leverage'),
            "sl_percent": sl_percent,
            "position": current_pos,
            "calculation_breakdown": calculation_breakdown
        })
    except Exception as e:
        print(f"❌ Error in api_coin_details: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@app.route("/api/today_stats")
@login_required
def api_today_stats():
    """Returns today's trade stats for the user"""
    try:
        stats = logic.get_today_stats(current_user.id)
        return jsonify(stats)
    except Exception as e:
        print(f"❌ Error fetching today stats: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/admin/reset_daily_trades", methods=["POST"])
@login_required
def admin_reset_daily_trades():
    """Admin only: Reset daily trade count for the current user"""
    # Check if admin
    is_admin = getattr(current_user, 'is_admin', False) or current_user.email.lower() in ['admin@mindriskcontrol.com', 'test@test.com']
    
    if not is_admin:
        return jsonify({"success": False, "message": "Unauthorized"}), 403
    
    try:
        today = datetime.utcnow().date().isoformat()
        stat = TradeDailyStats.query.filter_by(user_id=current_user.id, trade_date=today).first()
        if stat:
            stat.total_trades = 0
            stat.symbol_trades = '{}'
            db.session.commit()
            return jsonify({"success": True, "message": "Daily trades reset successfully"})
        return jsonify({"success": True, "message": "No trades found for today"})
    except Exception as e:
        db.session.rollback()
        print(f"❌ Error resetting daily trades: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@app.route("/validate_symbol/<symbol>")
@login_required
def validate_symbol_api(symbol):
    """
    ✅ NEW: Validate if a symbol exists and is tradeable
    Used to prevent UI errors before switching charts or feeds.
    """
    symbol = symbol.upper().strip()
    try:
        # Get all tradeable symbols
        all_symbols = logic.get_all_exchange_symbols(current_user.id)
        
        if symbol in all_symbols:
            # Symbol exists - try to get price to double-check it's tradeable
            price = logic.get_live_price(symbol, current_user.id)
            return jsonify({
                "valid": True, 
                "symbol": symbol,
                "tradeable": price > 0,
                "message": f"✅ {symbol} is valid and tradeable" if price > 0 else f"⚠️ {symbol} exists but price unavailable"
            })
        else:
            return jsonify({
                "valid": False, 
                "symbol": symbol,
                "tradeable": False,
                "message": f"❌ {symbol} is not a valid Binance Futures symbol"
            })
    except Exception as e:
        return jsonify({"valid": False, "error": str(e)}), 500

@app.route("/get_open_positions")
@login_required
@subscription_required
def get_open_positions_api():
    symbol_filter = request.args.get('symbol', '').strip().upper()
    fresh = request.args.get('fresh', '0') == '1'
    all_positions = logic.get_open_positions(current_user.id, force_refresh=fresh)
    
    # Filter by symbol if provided
    if symbol_filter and symbol_filter.strip():
        filtered_positions = [p for p in all_positions if isinstance(p, dict) and p.get('symbol') == symbol_filter]
        return jsonify({"positions": filtered_positions, "symbol": symbol_filter, "total": len(filtered_positions)})
    
    return jsonify({"positions": all_positions})

@app.route("/api/liquidation_prices")
@login_required
@subscription_required
def get_liquidation_prices_api():
    """LIVE liquidation prices - fetched fresh every time (NO CACHE)"""
    try:
        positions = logic.get_open_positions_live(current_user.id)  # Fresh fetch, no cache
        liquidation_data = {}
        for pos in positions:
            liquidation_data[pos['symbol']] = {
                'liquidation_price': pos['liquidation_price'],
                'mark_price': pos['mark_price'],
                'entry_price': pos['entry_price'],
                'leverage': pos['leverage'],
                'unrealized_pnl': pos['unrealized_pnl'],
                'roi_percent': pos['roi_percent'],
                'margin_ratio': pos.get('margin_ratio', 0),
                'timestamp': pos['timestamp']
            }
        return jsonify({"success": True, "liquidation_prices": liquidation_data})
    except Exception as e:
        print(f"Error fetching liquidation prices: {e}")
        return jsonify({"success": False, "error": str(e)})

@app.route("/get_trade_history")
@login_required
@subscription_required
def get_trade_history_api():
    fresh = request.args.get('fresh', '0') == '1'
    trades = logic.get_trade_history(current_user.id, force_refresh=fresh)
    symbol_filter = request.args.get('symbol', '').strip().upper()
    if symbol_filter and symbol_filter.strip():
        trades = [t for t in trades if isinstance(t, dict) and t.get('symbol') == symbol_filter]
    return jsonify({"trades": trades})

@app.route("/api/calculate-sizing")
@login_required
@subscription_required
def calculate_sizing_api():
    """
    ✅ NEW: Calculate position sizing for dynamic symbol changes
    Allows real-time sizing updates when user switches symbols
    """
    try:
        symbol = (request.args.get('symbol') or '').strip().upper()
        entry = float(request.args.get('entry') or 0)
        
        if not symbol or len(symbol) < 6:
            return jsonify({"success": False, "error": "Invalid symbol"}), 400
        
        if entry <= 0:
            return jsonify({"success": False, "error": "Invalid entry price"}), 400
        
        # Get balance info
        balance_data = logic.get_live_balance(current_user.id)
        balance = 0.0
        margin_used = 0.0
        
        if balance_data and isinstance(balance_data, tuple):
            inner_tuple = balance_data[0]
            if isinstance(inner_tuple, tuple) and len(inner_tuple) >= 2:
                balance = float(inner_tuple[0] or 0.0)
                margin_used = float(inner_tuple[1] or 0.0)
        
        unutilized = max(balance - margin_used, 0.0)
        
        # Get default SL type and value
        sl_type = request.args.get('sl_type', 'SL % Movement')
        sl_value = float(request.args.get('sl_value', 1.5))
        side = request.args.get('side', 'LONG')
        
        # Calculate sizing
        sizing = logic.calculate_position_sizing(
            unutilized, 
            entry, 
            sl_type, 
            sl_value, 
            side,
            user_id=current_user.id,
            symbol=symbol
        )
        
        return jsonify({
            "success": True,
            "symbol": symbol,
            "entry": entry,
            "suggested_units": sizing.get('suggested_units', 0),
            "suggested_leverage": sizing.get('suggested_leverage', 1),
            "margin_ratio": sizing.get('margin_ratio', 0),
            "liquidation_price": sizing.get('liquidation_price', 0)
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/get_user_trade_positions")
@login_required
@subscription_required
def get_user_trade_positions_api():
    """Fetch user's trade positions with TP/SL levels from database"""
    positions = logic.get_user_trade_positions_with_tp_sl(current_user.id)
    return jsonify({"positions": positions})

@app.route("/api/wallet_balance")
@login_required
def api_wallet_balance():
    """Fetch user's live wallet balance from Binance"""
    try:
        # Get user's exchange connection
        connection = ExchangeConnection.query.filter_by(
            user_id=current_user.id, 
            exchange_type='binance', 
            is_connected=True
        ).first()
        
        if not connection:
            return jsonify({"success": False, "error": "Exchange not connected"}), 400
            
        balance_data = logic.get_live_balance(current_user.id)
        if balance_data and isinstance(balance_data, tuple):
            inner_tuple = balance_data[0]
            if isinstance(inner_tuple, tuple) and len(inner_tuple) >= 2:
                balance = float(inner_tuple[0] or 0.0)
                margin_used = float(inner_tuple[1] or 0.0)
                unutilized = max(balance - margin_used, 0.0)
                return jsonify({
                    "success": True, 
                    "balance": balance, 
                    "margin_used": margin_used,
                    "unutilized": unutilized
                })
        
        return jsonify({"success": False, "error": "Failed to fetch balance"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/clear_trade_events", methods=["POST"])
@login_required
def clear_trade_events_api():
    if "trade_events" in session:
        session["trade_events"] = []
    return jsonify({"success": True})

@app.route("/api/trade_logs")
@login_required
def api_trade_logs():
    """Returns trade events for live log display"""
    try:
        # Get trade events from session or database
        events = session.get("trade_events", [])
        
        # If session empty, try to get recent history
        if not events:
            trades = logic.get_trade_history(current_user.id)
            if trades:
                events = [
                    {
                        "type": "TRADE_CLOSE" if trade.get("realized_pnl") else "TRADE_OPEN",
                        "symbol": trade.get("symbol", ""),
                        "timestamp": str(trade.get("time", "")),
                        "message": f"{trade.get('side')} {trade.get('symbol')} @ {trade.get('price')}",
                        "pnl": float(trade.get("realized_pnl", 0))
                    }
                    for trade in trades[:10]  # Last 10 trades
                ]
        
        return jsonify({"events": events})
    except Exception as e:
        print(f"❌ Error fetching trade logs: {e}")
        return jsonify({"events": []})

@app.route('/logout')
@login_required
def logout():
    user = User.query.get(current_user.id)
    if user:
        user.active_session = None
        db.session.commit()
    logout_user()
    session.clear()
    
    # Clear all session cookies
    response = make_response(redirect(url_for('login')))
    response.set_cookie('session', '', expires=0)
    response.set_cookie('remember_token', '', expires=0)
    
    flash("Logged out successfully.", "info")
    return response

@app.route('/index', methods=['GET', 'POST'])
@login_required
@subscription_required
def index():
    # 1. Initialize session if needed
    logic.initialize_session()
    
    # 2. Get available symbols
    symbols = logic.get_all_exchange_symbols(current_user.id)
    
    # 3. Get user balance & unutilized capital
    balance_data = logic.get_live_balance(current_user.id)
    balance = 0.0
    margin_used = 0.0
    
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
    
    # FIXED: Add missing today_stats computation
    today_stats = logic.get_today_stats(current_user.id)
    
    # -------------------------------------
    # ✅ CRITICAL FIX: Get symbol from URL query params and form data, use first symbol as default
    default_first_symbol = symbols[0] if symbols and len(symbols) > 0 else "BTCUSDT"
    selected_symbol = (request.args.get("symbol") or request.form.get("symbol") or default_first_symbol or "").strip().upper()
    previous_symbol = (request.form.get("prev_symbol") or "").strip().upper()
    symbol_changed = request.method == "POST" and bool(previous_symbol) and previous_symbol != selected_symbol
    
    # ✅ CRITICAL: Validate selected_symbol exists in available symbols or fallback
    if selected_symbol not in symbols and symbols:
        print(f"⚠️ Selected symbol '{selected_symbol}' not in available symbols, using first symbol: {symbols[0]}")
        selected_symbol = symbols[0]
    
    if not selected_symbol:
        selected_symbol = default_first_symbol
    
    print(f"✓ Index page loaded with symbol: {selected_symbol}")
    
    # ✅ CRITICAL FIX: Get live price for the SELECTED SYMBOL ONLY
    live_price = logic.get_live_price(selected_symbol, current_user.id)
    print(f"🔴 FETCHING PRICE FOR: {selected_symbol} = ${live_price}")
    
    side = request.form.get("side", "LONG")
    order_type = request.form.get("order_type", "MARKET")
    margin_mode = request.form.get("margin_mode", "ISOLATED")

    # ✅ CRITICAL FIX: Use the live price we just fetched
    submitted_entry = request.form.get("entry")
    entry_source = live_price if symbol_changed else (submitted_entry or live_price or 0)
    try:
        entry = float(entry_source or 0)
    except (TypeError, ValueError):
        entry = float(live_price or 0)

    if symbol_changed:
        print(f"🔄 Symbol changed {previous_symbol} → {selected_symbol}; using fresh live price ${entry}")

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

    sizing = logic.calculate_position_sizing(unutilized, entry, sl_type, sl_val, side, user_id=current_user.id, symbol=selected_symbol)
    trade_status = session.pop("trade_status", None)

    # ✅ EXECUTE TRADE if place_order button was clicked
    if "place_order" in request.form:
        user_units = float(request.form.get("user_units") or 0) or sizing.get("suggested_units", 0)
        user_lev = float(request.form.get("user_lev") or 0) or sizing.get("suggested_leverage", 1)
        
        result = logic.execute_trade_action(
            balance=balance,
            symbol=selected_symbol,
            side=side,
            entry=entry,
            order_type=order_type,
            sl_type=sl_type,
            sl_value=sl_val,
            sizing=sizing,
            user_units=user_units,
            user_lev=user_lev,
            margin_mode=margin_mode,
            tp1=tp1,
            tp1_pct=tp1_pct,
            tp2=tp2,
            user_id=current_user.id
        )
        
        session["trade_status"] = result
        # Invalidate ALL relevant caches so fresh data shows immediately after trade
        logic._positions_cache.pop(f"positions_{current_user.id}", None)
        logic._positions_cache_time.pop(f"positions_{current_user.id}", None)
        logic._trade_history_cache.pop(f"trade_history_{current_user.id}", None)
        logic._trade_history_cache_time.pop(f"trade_history_{current_user.id}", None)
        # CRITICAL FIX: Clear conditional orders cache so TP/SL appear immediately
        logic._conditional_cache.pop(current_user.id, None)
        return redirect(url_for("index", symbol=selected_symbol))

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

def ensure_sqlite_trade_positions_columns():
    """SQLite: add columns introduced after first deploy (create_all does not alter)."""
    try:
        from sqlalchemy import text
        with db.engine.begin() as conn:
            r = conn.execute(text("PRAGMA table_info(trade_positions)"))
            cols = [row[1] for row in r.fetchall()]
            if "opening_order_id" not in cols:
                conn.execute(text("ALTER TABLE trade_positions ADD COLUMN opening_order_id VARCHAR(64)"))
    except Exception as e:
        print(f"SQLite schema patch (trade_positions): {e}")

@app.route('/exchange-connections')
@login_required
def exchange_connections():
    # Get user's existing connections
    connections = ExchangeConnection.query.filter_by(user_id=current_user.id).all()
    
    # Format for UI
    formatted_connections = []
    for conn in connections:
        # Ensure last_verified is a datetime object or None
        last_verified_str = "Never"
        if conn.last_verified:
            if isinstance(conn.last_verified, datetime):
                last_verified_str = conn.last_verified.strftime("%Y-%m-%d %H:%M")
            else:
                # If it's already a string, just use it
                last_verified_str = str(conn.last_verified)

        formatted_connections.append({
            'id': conn.id,
            'exchange_type': conn.exchange_type,
            'connection_name': conn.connection_name or f"{conn.exchange_type.capitalize()} Connection",
            'is_connected': conn.is_connected,
            'last_verified': last_verified_str
        })
    
    # Supported exchanges list
    supported_exchanges = {
        'binance': {'name': 'Binance Futures', 'description': 'Connect your Binance Futures account'},
        'bybit': {'name': 'Bybit (Coming Soon)', 'description': 'Connect your Bybit account'},
        'okx': {'name': 'OKX (Coming Soon)', 'description': 'Connect your OKX account'}
    }
    
    return render_template('exchange_connections.html', 
                          connections=formatted_connections, 
                          supported_exchanges=supported_exchanges)

@app.route('/add-exchange', methods=['POST'])
@login_required
def add_exchange():
    data = request.get_json()
    exchange_type = data.get('exchange_type')
    api_key = data.get('api_key')
    api_secret = data.get('api_secret')
    connection_name = data.get('connection_name')
    
    if not exchange_type or not api_key or not api_secret:
        return jsonify({'success': False, 'message': 'Missing required fields'}), 400
    
    # Check if connection already exists
    existing = ExchangeConnection.query.filter_by(user_id=current_user.id, exchange_type=exchange_type).first()
    
    if existing:
        existing.api_key = api_key
        existing.api_secret = api_secret
        existing.connection_name = connection_name
        existing.is_connected = False # Reset status to verify
        conn = existing
    else:
        conn = ExchangeConnection(
            user_id=current_user.id,
            exchange_type=exchange_type,
            api_key=api_key,
            api_secret=api_secret,
            connection_name=connection_name
        )
        db.session.add(conn)
    
    try:
        db.session.commit()
        
        # Verify connection immediately
        # In logic.py, get_user_exchange_client will try to create a client
        # and set is_connected to True if successful
        logic.clear_user_client(current_user.id)
        client = logic.get_user_exchange_client(current_user.id)
        
        if client:
            conn.is_connected = True
            conn.last_verified = datetime.utcnow()
            db.session.commit()
            return jsonify({'success': True, 'message': f'Successfully connected to {exchange_type.capitalize()}!'})
        else:
            return jsonify({'success': False, 'message': 'Failed to verify API keys. Please check permissions.'})
            
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/disconnect-exchange/<int:conn_id>', methods=['POST'])
@login_required
def disconnect_exchange(conn_id):
    conn = ExchangeConnection.query.filter_by(id=conn_id, user_id=current_user.id).first()
    if not conn:
        return jsonify({'success': False, 'message': 'Connection not found'}), 404
    
    try:
        db.session.delete(conn)
        db.session.commit()
        logic.clear_user_client(current_user.id)
        return jsonify({'success': True, 'message': 'Exchange disconnected.'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/subscribe')
@login_required
def subscribe():
    is_admin = getattr(current_user, 'is_admin', False) or current_user.email.lower() in ['admin@mindriskcontrol.com', 'test@test.com']
    
    if is_admin:
        flash("You have admin access with unlimited features.", "info")
        return redirect(url_for('index'))
        
    return render_template('subscribe.html', user=current_user, key_id=config.RAZORPAY_KEY_ID)


@app.route('/create-subscription', methods=['POST'])
@login_required
def create_subscription():
    data = request.get_json(silent=True) or {}
    plan_type = data.get('plan_type', 'monthly')

    if plan_type == 'yearly':
        plan_id = config.RAZORPAY_YEARLY_PLAN_ID
        total_count = 1
    else:
        plan_id = config.RAZORPAY_MONTHLY_PLAN_ID
        total_count = 12

    try:
        subscription = razorpay_client.subscription.create({
            'plan_id': plan_id,
            'customer_notify': 1,
            'quantity': 1,
            'total_count': total_count,
        })
        return jsonify({'subscription_id': subscription['id']})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/verify-subscription', methods=['POST'])
@login_required
def verify_subscription():
    data = request.get_json(silent=True) or {}

    try:
        razorpay_client.utility.verify_payment_signature({
            'razorpay_payment_id': data['razorpay_payment_id'],
            'razorpay_subscription_id': data['razorpay_subscription_id'],
            'razorpay_signature': data['razorpay_signature']
        })

        plan_type = data.get('plan_type', 'monthly')
        duration_days = 30 if plan_type == 'monthly' else 365

        current_user.is_subscribed = True
        current_user.subscription_status = 'active'
        current_user.subscription_type = plan_type
        current_user.subscription_id = data.get('razorpay_subscription_id')
        current_user.subscription_start = datetime.utcnow()
        current_user.subscription_end = datetime.utcnow() + timedelta(days=duration_days)

        history = SubscriptionHistory(
            user_id=current_user.id,
            plan_type=plan_type,
            start_date=current_user.subscription_start,
            end_date=current_user.subscription_end,
            status='active'
        )
        db.session.add(history)
        db.session.commit()

        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/payment/create', methods=['POST'])
@login_required
def create_payment():
    plan_type = request.form.get('plan') # monthly/yearly
    
    amount = 490000 if plan_type == 'monthly' else 4900000 # In paise (INR 4,900 or 49,000)
    
    try:
        order = razorpay_client.order.create({
            'amount': amount,
            'currency': 'INR',
            'payment_capture': '1'
        })
        return jsonify(order)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/payment/verify', methods=['POST'])
@login_required
def verify_payment():
    data = request.get_json()
    
    # Verify signature
    try:
        razorpay_client.utility.verify_payment_signature({
            'razorpay_order_id': data['razorpay_order_id'],
            'razorpay_payment_id': data['razorpay_payment_id'],
            'razorpay_signature': data['razorpay_signature']
        })
        
        # Payment successful - Update user subscription
        plan_type = data.get('plan', 'monthly')
        duration_days = 30 if plan_type == 'monthly' else 365
        
        current_user.is_subscribed = True
        current_user.subscription_status = 'active'
        current_user.subscription_type = plan_type
        current_user.subscription_start = datetime.utcnow()
        current_user.subscription_end = datetime.utcnow() + timedelta(days=duration_days)
        
        # Log history
        history = SubscriptionHistory(
            user_id=current_user.id,
            plan_type=plan_type,
            start_date=current_user.subscription_start,
            end_date=current_user.subscription_end,
            status='active'
        )
        
        db.session.add(history)
        db.session.commit()
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/close_position/<symbol>', methods=['POST'])
@login_required
@subscription_required
def close_position_api(symbol):
    result = logic.close_position(symbol, current_user.id)
    return jsonify(result)

@app.route('/partial_close', methods=['POST'])
@login_required
@subscription_required
def partial_close_api():
    data = request.get_json()
    symbol = data.get('symbol')
    close_percent = float(data.get('close_percent', 50))
    result = logic.partial_close_position(symbol, close_percent=close_percent, user_id=current_user.id)
    return jsonify(result)

@app.route('/update_sl', methods=['POST'])
@login_required
@subscription_required
def update_sl_api():
    data = request.get_json()
    symbol = data.get('symbol')
    new_sl_percent = float(data.get('new_sl_percent', 0))
    # Note: In logic.py, update_stop_loss calls trail_stop_loss
    result = logic.update_stop_loss(symbol, new_sl_percent, current_user.id)
    return jsonify(result)

@app.route('/download_trades')
@login_required
@subscription_required
def download_trades():
    trades = logic.get_trade_history(current_user.id, force_refresh=True)
    
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Time', 'Symbol', 'Side', 'Qty', 'Price', 'PnL', 'Commission', 'OrderID', 'SL', 'CurrentSL', 'TP1', 'TP1%', 'TP2', 'TP2%', 'Status'])
    
    for trade in trades:
        writer.writerow([trade.get('time', ''), trade.get('symbol', ''), trade.get('side', ''), 
                        trade.get('qty', ''), trade.get('price', ''), trade.get('realized_pnl', ''), 
                        trade.get('commission', ''), trade.get('order_id', ''),
                        trade.get('sl_price', ''), trade.get('current_sl', ''),
                        trade.get('tp1_price', ''), trade.get('tp1_qty_pct', ''),
                        trade.get('tp2_price', ''), trade.get('remain_qty_pct', ''),
                        trade.get('position_status', '')])
    
    return Response(output.getvalue(), mimetype='text/csv', 
                   headers={'Content-Disposition': f'attachment; filename=trade_history_{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}.csv'})

@app.route('/create-admin')
def create_admin():
    """Debug route to create admin user test@test.com / Test@123"""
    try:
        admin_email = "test@test.com"
        admin_pass = "Test@123"
        
        user = User.query.filter_by(email=admin_email).first()
        if not user:
            user = User(
                username="admin_test",
                email=admin_email,
                password=generate_password_hash(admin_pass),
                is_admin=True,
                is_subscribed=True,
                subscription_status='active',
                subscription_end=datetime.utcnow() + timedelta(days=3650)
            )
            db.session.add(user)
            db.session.commit()
            return f"✅ Admin user {admin_email} created with password {admin_pass}. You can now login."
        else:
            user.is_admin = True
            user.is_subscribed = True
            user.subscription_status = 'active'
            user.subscription_end = datetime.utcnow() + timedelta(days=3650)
            db.session.commit()
            return f"✅ User {admin_email} already exists. Updated to Admin status. You can login with your password."
    except Exception as e:
        return f"❌ Error: {str(e)}"

@app.route('/test-binance')
@login_required
def test_binance():
    """Debug route to test Binance connectivity for current user"""
    try:
        client = logic.get_user_exchange_client(current_user.id)
        if not client:
            return "❌ No Binance connection found for your account."
        
        acc = client.futures_account(recvWindow=10000)
        balance = acc.get('totalWalletBalance', '0')
        return f"✅ Successfully connected! Your Futures Wallet Balance: {balance} USDT"
    except Exception as e:
        return f"❌ Connection Error: {str(e)}"

@app.route('/api/change_leverage', methods=['POST'])
@login_required
def change_leverage():
    """API endpoint to change leverage for a specific symbol"""
    try:
        data = request.get_json()
        symbol = data.get('symbol')
        leverage = data.get('leverage')
        
        if not symbol or not leverage:
            return jsonify({"success": False, "error": "Missing symbol or leverage"}), 400
            
        client = logic.get_user_exchange_client(current_user.id)
        if not client:
            return jsonify({"success": False, "error": "Exchange connection not found"}), 404
            
        # Change leverage on Binance
        client.futures_change_leverage(symbol=symbol, leverage=int(leverage))
        
        # Log the event
        logic.log_trade_event(current_user.id, f"⚡ Leverage for {symbol} changed to {leverage}x", "LEVERAGE_CHANGE")
        
        return jsonify({"success": True, "message": f"Leverage for {symbol} changed to {leverage}x"})
    except Exception as e:
        print(f"Error changing leverage: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/conditional_orders')
@login_required
def api_conditional_orders():
    try:
        orders = logic.get_all_open_conditional_orders(current_user.id)
    except Exception as e:
        return jsonify({"success": False, "error": str(e),
                        "conditional_orders": [], "basic_orders": []})
    # Split by Binance's Conditional vs Basic tab classification:
    # Conditional = TAKE_PROFIT_MARKET, STOP_MARKET, TAKE_PROFIT, STOP, TRAILING_STOP_MARKET (TP1, SL)
    # Basic       = LIMIT, LIMIT_MAKER (TP2)
    CONDITIONAL_TYPES = {
        'TAKE_PROFIT_MARKET', 'TAKE_PROFIT', 'STOP_MARKET',
        'STOP', 'TRAILING_STOP_MARKET', 'STOP_LOSS', 'STOP_LOSS_LIMIT'
    }
    conditional = [o for o in orders if o.get('type', '').upper() in CONDITIONAL_TYPES or o.get('source') == 'algo']
    basic = [o for o in orders if o.get('type', '').upper() not in CONDITIONAL_TYPES and o.get('source') != 'algo']
    return jsonify({"conditional_orders": conditional, "basic_orders": basic, "success": True})

@app.route('/api/tp1_and_sl_orders')
@login_required
def api_tp1_and_sl_orders():
    """
    Fetch TP1, TP2, and SL conditional orders with position context.
    Pass ?force=1 to bypass the server-side cache and fetch fresh data from Binance immediately.
    """
    from conditional_orders_enhancement import get_tp1_and_sl_orders, invalidate_cache
    try:
        if request.args.get('force') == '1':
            invalidate_cache(current_user.id)
        result = get_tp1_and_sl_orders(current_user.id)
    except Exception as e:
        result = {"success": False, "error": str(e), "tp1_orders": [], "tp2_orders": [], "sl_orders": []}
    return jsonify(result)


@app.route('/api/debug_conditional_orders')
@login_required
def api_debug_conditional_orders():
    """
    Debug: returns raw output of get_all_open_conditional_orders.
    Visit mindriskcontrol.com/api/debug_conditional_orders in your browser
    while logged in to see exactly what Binance is returning.
    Delete this route once confirmed working.
    """
    raw = logic.get_all_open_conditional_orders(current_user.id)
    return jsonify({"count": len(raw), "orders": raw})

@app.route('/api/cache_status', methods=['GET'])
@login_required
def api_cache_status():
    """Lightweight endpoint so the frontend status pill can show IP ban state and cache freshness."""
    import time
    now_ms = int(time.time() * 1000)
    banned = now_ms < logic._api_ban_until_ms
    return jsonify({
        "ip_banned": banned,
        "ban_until_ms": logic._api_ban_until_ms if banned else 0,
        "price_cache_ttl_s": 5,
        "balance_cache_ttl_s": 30,
        "conditional_cache_ttl_s": 30,
    })


@app.route('/api/raw_open_orders')
@login_required
def api_raw_open_orders():
    """
    Fetch ALL open orders from Binance (regular + conditional/algo) in raw form.
    Used by the Manual Cancel panel to show every live order.
    """
    try:
        client = logic.get_client(current_user.id)
        if not client:
            return jsonify({"success": False, "error": "No exchange connection", "orders": []})

        all_orders = []

        # Regular open orders
        try:
            regular = client.futures_get_open_orders(recvWindow=10000)
            for o in regular:
                all_orders.append({
                    "orderId": str(o.get('orderId', '')),
                    "symbol": o.get('symbol', ''),
                    "type": o.get('type', ''),
                    "side": o.get('side', ''),
                    "price": float(o.get('price') or 0),
                    "stopPrice": float(o.get('stopPrice') or 0),
                    "origQty": float(o.get('origQty') or 0),
                    "reduceOnly": o.get('reduceOnly', False),
                    "source": "regular",
                    "timeStr": datetime.fromtimestamp(int(o['time']) / 1000).strftime('%Y-%m-%d %H:%M:%S') if o.get('time') else 'N/A',
                })
        except Exception as e:
            print(f"[raw_open_orders] regular fetch error: {e}")

        # Conditional / algo orders
        try:
            if hasattr(client, '_request_futures_api'):
                algo_resp = client._request_futures_api('get', 'algo/openOrders', True, recvWindow=10000)
                algo_list = algo_resp if isinstance(algo_resp, list) else algo_resp.get('orders', [])
                for o in algo_list:
                    all_orders.append({
                        "orderId": str(o.get('algoId', o.get('orderId', ''))),
                        "symbol": o.get('symbol', ''),
                        "type": o.get('algoType', o.get('type', '')),
                        "side": o.get('side', ''),
                        "price": float(o.get('price') or 0),
                        "stopPrice": float(o.get('triggerPrice') or o.get('stopPrice') or 0),
                        "origQty": float(o.get('qty') or o.get('origQty') or 0),
                        "amount": float(o.get('amount') or 0),
                        "reduceOnly": o.get('reduceOnly', True),
                        "source": "algo",
                        "timeStr": datetime.fromtimestamp(int(o['bookTime']) / 1000).strftime('%Y-%m-%d %H:%M:%S') if o.get('bookTime') else 'N/A',
                    })
        except Exception as e:
            print(f"[raw_open_orders] algo fetch error: {e}")

        return jsonify({"success": True, "orders": all_orders, "count": len(all_orders)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e), "orders": []})


@app.route('/api/cancel_conditional_order', methods=['POST'])
@login_required
def api_cancel_conditional_order():
    data = request.get_json(silent=True) or {}
    order_id = data.get('order_id')
    symbol = data.get('symbol')
    source = data.get('source')
    if not order_id or not symbol:
        return jsonify({"success": False, "message": "Missing order_id or symbol"}), 400

    try:
        print(f"\n[API] 🔴 Cancel request: symbol={symbol}, order_id={order_id}, source={source}")
        
        # Use logic.cancel_order which handles both regular, algo, and virtual orders
        success, message = logic.cancel_order(symbol, order_id, current_user.id, source=source)
        
        # CRITICAL FIX: Only clear caches if Binance cancellation actually succeeded
        if success:
            print(f"[API] ✅ Cancel successful on Binance: {message}")
            logic._conditional_cache.pop(current_user.id, None)
            try:
                from conditional_orders_enhancement import invalidate_cache as _inv_tpsl
                _inv_tpsl(current_user.id)
            except Exception as inv_err:
                print(f"[API] Warning: Cache invalidation error: {inv_err}")
            return jsonify({"success": True, "message": message})
        else:
            print(f"[API] ❌ Cancel failed on Binance: {message}")
            # Return error but don't clear caches — order is still live on Binance
            return jsonify({"success": False, "message": message}), 400
    except Exception as e:
        print(f"[API] ❌ Exception in api_cancel_conditional_order: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "message": f"Server error: {str(e)}"}), 500

@app.route('/api/reconcile_orders', methods=['GET'])
@login_required
def api_reconcile_orders():
    """
    Reconciliation endpoint: compares DB open positions against live Binance orders.
    Non-destructive — reports mismatches only. Returns JSON with full mismatch detail.
    Call with ?fix=1 to auto-close stale DB positions that are no longer open on Binance.
    """
    try:
        result = logic.reconcile_binance_orders(current_user.id)

        # Optional: auto-close stale DB positions flagged by reconciliation
        if request.args.get('fix') == '1' and result.get('stale_db_positions'):
            from models import TradePosition
            fixed = []
            for item in result['stale_db_positions']:
                pos = TradePosition.query.get(item['db_id'])
                if pos and pos.status == 'open':
                    pos.status = 'closed'
                    pos.updated_at = datetime.utcnow()
                    fixed.append(item['symbol'])
            if fixed:
                db.session.commit()
                # Invalidate order caches so UI reflects changes immediately
                logic._conditional_cache.pop(current_user.id, None)
                try:
                    from conditional_orders_enhancement import invalidate_cache
                    invalidate_cache(current_user.id)
                except Exception:
                    pass
                result['auto_fixed'] = fixed
                result['summary'] += f" — Auto-closed {len(fixed)} stale DB position(s): {', '.join(fixed)}"
                print(f"[reconcile/fix] Auto-closed stale positions: {fixed}")

        return jsonify(result)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        ensure_sqlite_trade_positions_columns()
    app.run(host='0.0.0.0', port=5000, debug=False)
