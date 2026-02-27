from flask import Flask, render_template, request, session, jsonify, redirect, url_for, Response
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from authlib.integrations.flask_client import OAuth
from dotenv import load_dotenv
from datetime import datetime, timedelta
from functools import wraps
from flask import redirect, url_for, flash
from flask_login import current_user
from datetime import datetime, timedelta
from models import db, User
import logic
import config
import os
import csv
import io
from flask import session
import uuid
import razorpay
import hashlib
import hmac

app = Flask(__name__)
app.secret_key = "trading_secret_key_ultra_secure_2025"


# Database & Login Configuration
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'sqlite:///users.db').replace("postgres://", "postgresql://", 1)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SESSION_PERMANENT'] = False

# ✅ FIX: Get Plan IDs from config.py directly
RAZORPAY_MONTHLY_PLAN_ID = config.RAZORPAY_MONTHLY_PLAN_ID
RAZORPAY_YEARLY_PLAN_ID = config.RAZORPAY_YEARLY_PLAN_ID

db.init_app(app)
login_manager = LoginManager()
login_manager.login_view = 'login'
login_manager.init_app(app)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))
# Google OAuth Setup
oauth = OAuth(app)
google = oauth.register(
    name='google',
    client_id=os.getenv('GOOGLE_CLIENT_ID'),
    client_secret=os.getenv('GOOGLE_CLIENT_SECRET'),
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'}
)

# Razorpay Client Setup
razorpay_client = razorpay.Client(auth=(config.RAZORPAY_KEY_ID, config.RAZORPAY_KEY_SECRET))

def get_month_end(dt=None):
    if not dt:
        dt = datetime.utcnow()
    next_month = dt.replace(day=28) + timedelta(days=4)  # always goes to next month
    return next_month.replace(day=1) - timedelta(seconds=1)



# --- 2. THE GATEKEEPER (Checks Expiry Date) ---
def subscription_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for('login'))
        
        # Check if subscription exists AND if it has expired
        now = datetime.utcnow()
        if not current_user.is_subscribed or (current_user.subscription_end and now > current_user.subscription_end):
            # Auto-reset status if expired
            if current_user.is_subscribed:
                current_user.is_subscribed = False
                db.session.commit()
            
            flash("Your subscription has expired. Please renew to access the dashboard.", "warning")
            return redirect(url_for('subscribe'))
            
        return f(*args, **kwargs)
    return decorated_function

# --- PUBLIC PAGES ---

@app.route('/') # Change this from /home to /
def home():
    """Public homepage - the first thing users see"""
    return render_template('home.html')

@app.route('/home') # Keep this as an alias
def home_alias():
    return redirect(url_for('home'))

@app.route('/about')
def about():
    """About page"""
    return render_template('about.html')

@app.route('/contact')
def contact():
    """Contact page"""
    return render_template('contact.html')

@app.route('/terms')
def terms():
    """Terms & Conditions page"""
    return render_template('terms.html')

@app.route('/privacy')
def privacy():
    """Privacy Policy page"""
    return render_template('privacy.html')


# --- AUTHENTICATION ROUTES ---

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
            return redirect(url_for('login')) 

    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')

        user = User.query.filter_by(email=email).first()

        if not user or not check_password_hash(user.password, password):
            flash("Invalid email or password", "error")
            return render_template('login.html')

        # 🔒 Block multiple logins
        if user.active_session:
            flash("This account is already logged in. Please logout first.", "error")
            return redirect(url_for('login'))

        # ✅ LOGIN USER
        login_user(user)

        # 🎁 FIRST LOGIN / TRIAL → VALID TILL MONTH END
        if not user.subscription_end:
            user.is_subscribed = True
            user.subscription_type = "trial"
            user.subscription_status = "active"
            user.subscription_start = datetime.utcnow()
            user.subscription_end = get_month_end()

        # 🔑 Track session
        session_id = str(uuid.uuid4())
        session['session_id'] = session_id
        user.active_session = session_id

        db.session.commit()
        return redirect(url_for('index'))

    return render_template('login.html')
def dashboard_defaults():
    return {
        "trade_status": None,
        "default_side": "LONG",
        "selected_symbol": "BTCUSDT",
        "unutilized": 0.0,
        "today_stats": {
            "total_trades": 0,
            "max_trades": 10
        },
        "symbols": ["BTCUSDT", "ETHUSDT"],
        "order_type": "MARKET",
        "margin_mode": "ISOLATED",
        "default_entry": 0,
        "tp1": "",
        "tp1_pct": "",
        "tp2": "",
        "default_sl_type": "SL Points",
        "default_sl_value": 0,
        "sizing": {
            "suggested_units": 0,
            "suggested_leverage": 1,
            "risk_amount": 0,
            "error": None
        }
    }
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
    # Redirect to subscription page instead of index
    return redirect(url_for('subscribe'))

@app.route('/logout')
@login_required
def logout():
    current_user.active_session = None
    db.session.commit()

    logout_user()
    session.pop('session_id', None)

    return redirect(url_for('login'))

# --- RAZORPAY SUBSCRIPTION ROUTES ---

@app.route('/subscribe')
@login_required
def subscribe():
    """Render subscription page with Razorpay key"""
    return render_template('subscribe.html', key_id=config.RAZORPAY_KEY_ID, user=current_user)

@app.route('/create-subscription', methods=['POST'])
@login_required
def create_subscription():
    """Create a Razorpay subscription"""
    try:
        data = request.get_json()
        plan_type = data.get("plan_type")

        if not plan_type:
            return jsonify({
                "success": False,
                "error": "plan_type is required"
            }), 400

        # ✅ Select correct Razorpay Plan ID
        if plan_type == "monthly":
            plan_id = RAZORPAY_MONTHLY_PLAN_ID
        elif plan_type == "yearly":
            plan_id = RAZORPAY_YEARLY_PLAN_ID
        else:
            return jsonify({
                "success": False,
                "error": "Invalid plan type"
            }), 400

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
        return jsonify({
            "success": False,
            "error": str(e)
        }), 400

# Rename this specific block in app.py
@app.route('/verify-subscription', methods=['POST'])
@login_required
def verify_subscription():
    try:
        data = request.get_json()

        # 1️⃣ Verify Razorpay signature (SECURITY)
        razorpay_client.utility.verify_subscription_payment_signature({
            "razorpay_payment_id": data.get("razorpay_payment_id"),
            "razorpay_subscription_id": data.get("razorpay_subscription_id"),
            "razorpay_signature": data.get("razorpay_signature")
        })

        # 2️⃣ Get plan type saved during creation
        plan_type = session.pop("pending_plan_type", "monthly")

        # 3️⃣ Decide duration
        duration_days = 365 if plan_type == "yearly" else 30

        # 4️⃣ Update user subscription details
        current_user.is_subscribed = True
        current_user.subscription_id = data.get("razorpay_subscription_id")
        current_user.subscription_status = "active"
        current_user.subscription_type = plan_type
        current_user.subscription_start = datetime.utcnow()
        current_user.subscription_end = datetime.utcnow() + timedelta(days=duration_days)

        db.session.commit()

        return jsonify({
            "success": True,
            "plan": plan_type,
            "valid_till": current_user.subscription_end.strftime("%Y-%m-%d")
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
    """Check user's subscription status"""
    if current_user.is_subscribed:
        if current_user.subscription_end and current_user.subscription_end > datetime.utcnow():
            return jsonify({
                'subscribed': True,
                'status': current_user.subscription_status,
                'end_date': current_user.subscription_end.strftime('%Y-%m-%d')
            })
        else:
            current_user.subscription_status = 'expired'
            current_user.is_subscribed = False
            db.session.commit()
            return jsonify({
                'subscribed': False,
                'status': 'expired'
            })
    
    return jsonify({
        'subscribed': False,
        'status': 'inactive'
    })

# --- ORIGINAL TRADING ROUTES ---

@app.route("/get_live_price/<symbol>")
@login_required
@subscription_required
def live_price_api(symbol):
    price = logic.get_live_price(symbol)
    return jsonify({"price": price if price else 0})

@app.route("/get_open_positions")
@login_required
def get_open_positions_api():
    positions = logic.get_open_positions()
    return jsonify({"positions": positions})

@app.route("/get_trade_history")
@login_required
def get_trade_history_api():
    trades = logic.get_trade_history()
    return jsonify({"trades": trades})

@app.route("/get_today_stats")
@login_required
def get_today_stats_api():
    stats = logic.get_today_stats()
    return jsonify(stats)

@app.route("/close_position/<symbol>", methods=["POST"])
@login_required
def close_position_api(symbol):
    result = logic.close_position(symbol)
    return jsonify(result)

@app.route("/partial_close", methods=["POST"])
@login_required
def partial_close_api():
    data = request.get_json()
    symbol = data.get('symbol')
    close_percent = data.get('close_percent')
    close_qty = data.get('close_qty')
    if not symbol:
        return jsonify({"success": False, "message": "Symbol required"})
    result = logic.partial_close_position(symbol, close_percent, close_qty)
    return jsonify(result)

@app.route("/update_sl", methods=["POST"])
@login_required
def update_sl_api():
    data = request.get_json()
    symbol = data.get('symbol')
    new_sl_percent = float(data.get('new_sl_percent', 0))
    if not symbol:
        return jsonify({"success": False, "message": "Symbol required"})
    result = logic.update_stop_loss(symbol, new_sl_percent)
    return jsonify(result)

@app.route("/download_trades")
@login_required
def download_trades():
    trades = logic.get_trade_history()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Time (UTC)', 'Symbol', 'Side', 'Quantity', 'Price', 'Realized PnL', 'Commission', 'Order ID'])
    for trade in trades:
        writer.writerow([trade.get('time', ''), trade.get('symbol', ''), trade.get('side', ''), trade.get('qty', ''), trade.get('price', ''), trade.get('realized_pnl', ''), trade.get('commission', ''), trade.get('order_id', '')])
    output.seek(0)
    return Response(output.getvalue(), mimetype='text/csv', headers={'Content-Disposition': f'attachment; filename=trade_history_{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}.csv'})



@app.route("/index", methods=["GET", "POST"])
@login_required
@subscription_required
def index():
    logic.initialize_session()
    symbols = logic.get_all_exchange_symbols()
    live_bal, live_margin = logic.get_live_balance()

    balance = live_bal or 0.0
    margin_used = live_margin or 0.0
    unutilized = max(balance - margin_used, 0.0)
 
    logic.initialize_session()
    symbols = logic.get_all_exchange_symbols()
    

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
            balance, selected_symbol, side, entry, order_type, sl_type, sl_val, sizing,
            float(request.form.get("user_units") or 0), float(request.form.get("user_lev") or 0),
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

# Database initialization
with app.app_context():
    db.create_all()

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000, debug=True)
