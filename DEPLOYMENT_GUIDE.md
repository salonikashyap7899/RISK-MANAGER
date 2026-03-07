# 🚀 Deployment Guide for Hostinger (mindriskcontrol.com)

## Files to Upload to Hostinger

Upload these files to your Hostinger public_html or appropriate directory:

```
📁 Upload to Hostinger File Manager:
├── app.py
├── auth.py
├── billing.py
├── calculations.py
├── config.py
├── email_utils.py
├── logic.py
├── models.py
├── requirements.txt
├── Procfile
├── templates/ (entire folder)
├── static/ (entire folder)
└── instance/ (if exists)
```

---

## Step 1: Upload Files via Hostinger File Manager

1. Go to **Hostinger hPanel** → **Files** → **File Manager**
2. Navigate to **public_html**
3. Click **Upload** and select all project files
4. Make sure to upload the **templates/** and **static/** folders with all contents

---

## Step 2: Set Up Python on Hostinger

### Option A: Using Hostinger Python App (Recommended)

1. Go to **Hostinger hPanel** → **Website** → **Python**
2. Click **Create Python App**
3. Select your domain: `mindriskcontrol.com`
4. Choose Python version: **3.11** or **3.10**
5. Click **Create**

### Option B: Using SSH Terminal

1. Go to **Hostinger hPanel** → **Advanced** → **SSH Access**
2. Connect via SSH and run:

```bash
cd /home/yourusername/mindriskcontrol.com
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

---

## Step 3: Configure Environment Variables

In Hostinger Python app settings or `.env` file, add:

```
# Flask Configuration
FLASK_APP=app.py
SECRET_KEY=your_secret_key_here

# Database
DATABASE_URL=sqlite:///users.db
# OR use Hostinger MySQL:
# DATABASE_URL=mysql://username:password@host/databasename

# Google OAuth (get from Google Cloud Console)
GOOGLE_CLIENT_ID=your_google_client_id
GOOGLE_CLIENT_SECRET=your_google_client_secret

# Razorpay (get from Razorpay Dashboard)
RAZORPAY_KEY_ID=rzp_live_xxxxxxx
RAZORPAY_KEY_SECRET=your_razorpay_secret
RAZORPAY_MONTHLY_PLAN_ID=plan_xxxxx
RAZORPAY_YEARLY_PLAN_ID=plan_xxxxx

# Binance (Optional - for demo/backward compatibility)
BINANCE_KEY=your_binance_key
BINANCE_SECRET=your_binance_secret
```

---

## Step 4: Update config.py for Production

Your config.py already uses environment variables, so it will work with the above settings!

---

## Step 5: Set Up MySQL Database (Recommended)

1. Go to **Hostinger hPanel** → **Databases** → **MySQL**
2. Create a new database
3. Note the: hostname, username, password, database name
4. Update DATABASE_URL in environment variables:
```
DATABASE_URL=mysql://mysql_username:password@hostname/database_name
```

---

## Step 6: Configure Domain & SSL

1. Go to **Hostinger hPanel** → **Websites** → **Domains**
2. Ensure `mindriskcontrol.com` points to your Python app
3. SSL Certificate should be auto-installed by Hostinger

---

## Step 7: Start the Application

In Hostinger Python app:
- **Command:** `python app.py`
- **App Mode:** Production

Or via SSH:
```bash
source venv/bin/activate
python app.py
```

---

## ⚠️ Important Razorpay Settings

For payments to work on mindriskcontrol.com:

1. **Razorpay Dashboard** → **Account Settings** → **Webhook**
2. Add Webhook:
   - URL: `https://mindriskcontrol.com/verify-subscription`
   - Events: `subscription.activated`, `subscription.cancelled`, `payment.captured`
3. **Website Settings** → **Payment Gateway**
4. Enable test mode for testing

---

## 🔧 Quick Fix - Disable Hardcoded Keys

Remove hardcoded keys from config.py. The current config.py already uses `os.getenv()` which is correct!

---

## Testing Your Live Site

1. **Register** at: https://mindriskcontrol.com/register
2. **Login** at: https://mindriskcontrol.com/login
3. **Subscribe** - You can use Razorpay test card:
   - Card Number: `4242 4242 4242 4242`
   - Expiry: Any future date (e.g., 12/28)
   - CVV: `123`
   - OTP: `123456`

4. **Connect Exchange** at: https://mindriskcontrol.com/exchange-connections
   - Enter your own Binance API keys

5. **Start Trading** at: https://mindriskcontrol.com/index

---

## Troubleshooting

### "Application Error"
→ Check Python app logs in Hostinger panel

### "Database Connection Error"
→ Verify DATABASE_URL is correct

### "Payment Failed"
→ Verify Razorpay webhook is configured

### "Exchange Connection Failed"
→ User needs to enable Futures permission on their Binance API
