# models.py
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime

db = SQLAlchemy()

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(100), unique=True, nullable=False)
    username = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=True) 
    google_id = db.Column(db.String(200), unique=True, nullable=True)

    # Subscription Fields
    is_subscribed = db.Column(db.Boolean, default=False)
    subscription_plan = db.Column(db.String(50), default="free")
    
    # Razorpay Specific Fields
    razorpay_subscription_id = db.Column(db.String(100), nullable=True)
    razorpay_payment_id = db.Column(db.String(100), nullable=True)
    subscription_status = db.Column(db.String(20), default="inactive") # active, authenticated, etc.
    subscription_start = db.Column(db.DateTime, nullable=True)
    subscription_end = db.Column(db.DateTime, nullable=True)