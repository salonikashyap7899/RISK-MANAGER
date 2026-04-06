# models.py
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime
import json

db = SQLAlchemy()

class User(UserMixin, db.Model):
    __tablename__ = 'user'
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(100), unique=True, nullable=False)
    username = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=True) 
    google_id = db.Column(db.String(200), unique=True, nullable=True)
    active_session = db.Column(db.String(255), nullable=True)
    is_admin = db.Column(db.Boolean, default=False)
    is_subscribed = db.Column(db.Boolean, default=False)
    subscription_id = db.Column(db.String(200), nullable=True)
    subscription_status = db.Column(db.String(50), default='inactive')
    subscription_type = db.Column(db.String(20), nullable=True)
    subscription_start = db.Column(db.DateTime, nullable=True)
    subscription_end = db.Column(db.DateTime, nullable=True)
    is_paused = db.Column(db.Boolean, default=False)
    paused_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class SubscriptionHistory(db.Model):
    __tablename__ = 'subscription_history'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    plan_type = db.Column(db.String(20), nullable=False)
    start_date = db.Column(db.DateTime, nullable=False)
    end_date = db.Column(db.DateTime, nullable=False)
    status = db.Column(db.String(20), default='active')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class ExchangeConnection(db.Model):
    __tablename__ = 'exchange_connections'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    exchange_type = db.Column(db.String(50), nullable=False)
    api_key = db.Column(db.String(500), nullable=True)
    api_secret = db.Column(db.String(500), nullable=True)
    additional_data = db.Column(db.Text, nullable=True)
    is_connected = db.Column(db.Boolean, default=False)
    last_verified = db.Column(db.DateTime, nullable=True)
    connection_name = db.Column(db.String(100), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class TradeDailyStats(db.Model):
    __tablename__ = 'trade_daily_stats'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    trade_date = db.Column(db.String(10), nullable=False)
    total_trades = db.Column(db.Integer, default=0)
    symbol_trades = db.Column(db.Text, default='{}')
    __table_args__ = (db.UniqueConstraint('user_id', 'trade_date', name='unique_user_date'),)
    
    def get_symbol_trades(self):
        return json.loads(self.symbol_trades) if self.symbol_trades else {}
    def set_symbol_trades(self, data):
        self.symbol_trades = json.dumps(data)
    @classmethod
    def get_for_user(cls, user_id, date_str):
        stat = cls.query.filter_by(user_id=user_id, trade_date=date_str).first()
        if not stat:
            stat = cls(user_id=user_id, trade_date=date_str)
            db.session.add(stat)
            db.session.commit()
        return stat

class TradeLog(db.Model):
    __tablename__ = 'trade_log'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    event_type = db.Column(db.String(50), nullable=False)
    message = db.Column(db.Text, nullable=False)
    pnl = db.Column(db.Float, default=0.0)

    @classmethod
    def get_recent(cls, user_id, limit=50):
        return cls.query.filter_by(user_id=user_id).order_by(cls.timestamp.desc()).limit(limit).all()

class TradePosition(db.Model):
    __tablename__ = 'trade_positions'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    symbol = db.Column(db.String(20), nullable=False)
    side = db.Column(db.String(10), nullable=False)
    entry_price = db.Column(db.Float, nullable=False)
    initial_qty = db.Column(db.Float, nullable=False)
    remain_qty_pct = db.Column(db.Float, default=100.0)
    sl_price = db.Column(db.Float, nullable=False)
    sl_trail_pct = db.Column(db.Float, default=0.0)
    tp1_price = db.Column(db.Float, nullable=True)
    tp1_qty_pct = db.Column(db.Float, default=0.0)
    tp2_price = db.Column(db.Float, nullable=True)
    current_sl = db.Column(db.Float, nullable=False)
    suggested_leverage = db.Column(db.Integer, default=1)
    unrealized_pnl = db.Column(db.Float, default=0.0)
    status = db.Column(db.String(20), default='open')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def update_trail_sl(self, new_sl):
        self.current_sl = new_sl
        self.sl_trail_pct = ((self.entry_price - new_sl) / self.entry_price * 100) if self.side == 'LONG' else ((new_sl - self.entry_price) / self.entry_price * 100)
        self.updated_at = datetime.utcnow()

    @classmethod
    def get_open(cls, user_id):
        return cls.query.filter_by(user_id=user_id, status='open').all()