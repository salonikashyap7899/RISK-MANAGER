# 🧪 Testing Guide - Multi-Exchange Implementation

## Quick Start

### Step 1: Run the Application
```bash
cd "c:/Users/isalo/Downloads/mindriskcontrol/Trade-flask-fixed (1)"
python app.py
```

### Step 2: Open Browser
Go to: `http://localhost:5000`

---

## Testing Flow

### 1. Register a New Account
- Click "Register" on homepage
- Enter: email, username, password
- Click Submit

### 2. Login
- Use registered credentials
- You'll be redirected to subscribe page

### 3. Subscribe (Razorpay - ₹500/month)
- Choose Monthly or Yearly plan
- Complete payment (use test card: 4242 4242 4242 4242)
- After payment, redirected to dashboard

### 4. Connect Exchange (Key Feature!)
- Go to: `http://localhost:5000/exchange-connections`
- Click "Connect" on Binance
- Enter your API Key and Secret
- Click Connect

---

## How to Get Binance API Keys

1. **Login to Binance**: https://www.binance.com
2. **Go to API Management**: Account → API
3. **Create API Key**:
   - Set label: "Pro Trading"
   - Verify with 2FA
4. **Enable Futures Permission**:
   - Edit API Key permissions
   - Enable "Futures" trading
5. **Copy Keys**:
   - API Key: starts with `vm...`
   - Secret Key: keep safe!

---

## What to Verify

| Feature | Expected Behavior |
|---------|------------------|
| ✅ Registration | New user can register |
| ✅ Login | User can login |
| ✅ Subscription | Payment flow works |
| ✅ Exchange Connection | Can add Binance API |
| ✅ Trading | Trades on user's account |
| ✅ Disconnect | Can remove exchange |

---

## Important Notes

⚠️ **Test with Small Amounts First**
- Use Binance Testnet for testing
- Testnet URL: https://testnet.binancefuture.com

⚠️ **API Key Requirements**
- Must have Futures trading permission
- IP restriction should be disabled OR add your server IP

⚠️ **Security**
- Never share API secrets
- Users trade with THEIR own money
- You (the platform owner) have NO access to funds

---

## Troubleshooting

### "Invalid API Key" Error
→ Check if Futures permission is enabled on Binance

### "Connection Failed" Error
→ Verify API key is correct
→ Check IP restrictions

### "Insufficient Balance"
→ User needs to deposit funds in their exchange account

---

## User Flow Summary

```
1. User registers → 2. User logs in → 3. User subscribes (₹500) 
         ↓
4. User connects their exchange (Binance/Bybit/etc)
         ↓
5. User can now trade → All trades on USER'S account
```

This approach means:
- ✅ You provide the trading tool
- ✅ Users use their own money
- ✅ No liability for you
- ✅ Scalable to unlimited users
