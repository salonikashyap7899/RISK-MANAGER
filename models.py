from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime

db = SQLAlchemy()

class User(UserMixin, db.Model):
    __tablename__ = 'user'
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(100), unique=True, nullable=False)
    username = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=True) 
    google_id = db.Column(db.String(200), unique=True, nullable=True)
    
    # Session Management
    active_session = db.Column(db.String(255), nullable=True)
    is_admin = db.Column(db.Boolean, default=False)
    
    # Subscription - Current Status
    is_subscribed = db.Column(db.Boolean, default=False)
    subscription_id = db.Column(db.String(200), nullable=True) # Razorpay ID
    subscription_status = db.Column(db.String(50), default='inactive') # active/expired
    subscription_type = db.Column(db.String(20), nullable=True) # monthly/yearly/trial
    subscription_start = db.Column(db.DateTime, nullable=True)
    subscription_end = db.Column(db.DateTime, nullable=True)

    # Pause Feature
    is_paused = db.Column(db.Boolean, default=False)
    paused_at = db.Column(db.DateTime, nullable=True)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# THIS WAS MISSING - This fixes your ImportError
class SubscriptionHistory(db.Model):
    __tablename__ = 'subscription_history'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    plan_type = db.Column(db.String(20), nullable=False) # trial / monthly / yearly
    start_date = db.Column(db.DateTime, nullable=False)
    end_date = db.Column(db.DateTime, nullable=False)
    status = db.Column(db.String(20), default='active')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# Exchange Connection Model - Store user's exchange API credentials
class ExchangeConnection(db.Model):
    __tablename__ = 'exchange_connections'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    # Exchange Type: binance, metatrader, upstox, bybit, okx
    exchange_type = db.Column(db.String(50), nullable=False)
    
    # API Credentials (encrypted in production - storing plain for demo)
    api_key = db.Column(db.String(500), nullable=True)
    api_secret = db.Column(db.String(500), nullable=True)
    
    # Additional fields based on exchange
    additional_data = db.Column(db.Text, nullable=True)  # JSON string for extra params
    
    # Connection Status
    is_connected = db.Column(db.Boolean, default=False)
    last_verified = db.Column(db.DateTime, nullable=True)
    
    # Nickname for user's reference
    connection_name = db.Column(db.String(100), nullable=True)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
