# Run this script to reset all user subscriptions
# This will set is_subscribed = False for all users, forcing them to subscribe again

from app import app
from models import db, User

with app.app_context():
    # Reset all users' subscription status
    users = User.query.all()
    for user in users:
        user.is_subscribed = False
        user.subscription_status = 'inactive'
        user.subscription_type = None
        user.subscription_start = None
        user.subscription_end = None
        user.subscription_id = None
        user.active_session = None
        print(f"Reset subscription for user: {user.email}")
    
    db.session.commit()
    print("\nAll user subscriptions have been reset!")
    print("Users will now need to subscribe to access the dashboard.")
