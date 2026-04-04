# MindRiskControl Trading Features Implementation Guide

**Last Updated:** April 4, 2026  
**Status:** ✅ All features implemented with confirmations and validation

---

## Table of Contents
1. [Overview](#overview)
2. [Core Features](#core-features)
3. [Step-by-Step Implementation](#step-by-step-implementation)
4. [Troubleshooting](#troubleshooting)

---

## Overview

This application implements a complete **Binance Futures trading system** with automatic position sizing, risk management, and multi-level take profit/stop loss orders.

### Key Components
- **Backend:** Flask + Python + Binance API (`python-binance`)
- **Frontend:** HTML/CSS/JavaScript with TradingView charts
- **Database:** SQLAlchemy for user management
- **External APIs:** Binance Futures REST API v1/v2

---

## Core Features

### 1. **Order Types**
✅ **MARKET Orders** - Execute immediately at market price  
✅ **LIMIT Orders** - Execute at a specified price (GTC - Good Till Cancel)

### 2. **Position Management**
✅ **Entry Price** - Manual entry or live price fetching  
✅ **Stop Loss (SL)** - Mandatory protection with two modes:
   - **SL Points** - Fixed distance in USDT
   - **SL % Movement** - Percentage-based distance

✅ **Take Profit Levels**
   - **TP1** - Partial take profit with custom exit percentage
   - **TP2** - Full position close at target price

✅ **Leverage Override** - Manual leverage setting (1-125x)  
✅ **Position Override** - Manual quantity override

### 3. **Risk Management**
✅ **Position Sizing** - Automatic calculation based on:
   - Risk per trade: 1% of unutilized margin
   - Stop loss distance
   - Entry price
   - Maximum leverage (calculated as 100/(SL% + 0.2))

✅ **Daily Trade Limits**
   - Maximum trades per day: Configurable
   - Maximum trades per symbol: Configurable

✅ **Minimum Order Validation**
   - Checks Binance minimum notional (≥ $5 USDT)
   - Validates quantity minimum (LOT_SIZE filter)
   - Prevents API errors before order submission

### 4. **Real-Time Dashboard**
✅ **Live Positions** - Updated every 10 seconds (cached 30s)
   - Symbol, leverage, entry price
   - Current P&L and ROI
   - Margin usage
   - Open orders per position

✅ **Trade History** - Last 10 trades (cached 60s)
   - Time, symbol, side, price, quantity, realized P&L

✅ **Live Trade Log** - Real-time event stream
   - Trade opens/closes
   - SL updates
   - Partial closes

✅ **Live Price Updates** - Every 5 seconds (cached 10s)
   - Auto-fill entry price from Binance

### 5. **Confirmation System**
✅ **Trade Execution Confirmation**
   - Shows full order details before execution
   - Confirms: symbol, side, entry, SL, TP1, TP2, qty, leverage

✅ **Position Management Confirmations**
   - Close full position
   - Partial close (with percentage input)
   - Trailing SL update

✅ **Form Validation**
   - Real-time warnings for invalid inputs
   - Validates SL direction (LONG/SHORT)
   - Checks TP levels are valid
   - Warns if order size below Binance minimum

---

## Step-by-Step Implementation

### Step 1: Environment Setup
```bash
cd "c:\Users\isalo\Downloads\mindriskcontrol\Trade-flask-fixed (1)"
source .venv/Scripts/activate
pip install -r requirements.txt
```

### Step 2: Configure Binance Connection
1. Go to **Settings** → **Exchange Connections**
2. Paste your Binance API Key with Futures enabled
3. Paste your Binance API Secret
4. Click **Connect**

**⚠️ Important:** API keys must have these permissions:
- `Exchange/Trading` ✅
- `Futures/Trading` ✅ (IMPORTANT!)
- `Future Reading` ✅
- Do NOT enable account withdrawal

### Step 3: Place Your First Trade

#### Part A: Set Entry Price
```
1. Select Symbol → e.g., BTCUSDT
2. Click "🔄 Live" button → Fills with current market price
3. Or manually enter entry price
```

#### Part B: Set Stop Loss (MANDATORY)
```
1. Choose SL Method:
   - "SL Points" → Enter distance in USDT (e.g., 500)
   - "SL % Movement" → Enter percentage (e.g., 1.5)
2. SL Value field will show your stop loss price
3. SL button will unlock when SL > 0
```

#### Part C: Configure TP Levels (optional)
```
TP1 (Partial Take Profit):
  - Type: "TP1 Price" or "TP1 % Movement"
  - Value: Target price or percentage
  - Qty %: Exit percentage (default 50%)
  
TP2 (Full Position Close):
  - Enter target price only
  - Will close 100% when hit
```

#### Part D: Position Sizing
```
Left column (sidebar):
- Shows suggested units (auto-calculated)
- Shows suggested leverage
- Shows formula: Risk ÷ (SL% + 0.2) × 100

Override fields (optional):
- Pos Override: Manual quantity
- Lev Override: Manual leverage
```

#### Part E: Execute Trade
```
1. Review confirmation dialog:
   - Symbol, Side (LONG/SHORT)
   - Entry, SL, TP1, TP2
   - Qty, Leverage
   
2. Click "✅ EXECUTE EXCHANGE ORDER"
3. Binance order will execute
4. SL and TP orders placed automatically
```

### Step 4: Manage Open Positions

#### Monitor Live Positions
```
Right panel → "POSITIONS (n)"
Shows for each trade:
- Symbol, Leverage, Entry Price
- Current P&L ($) and ROI (%)
- Margin Used | Size
- Open Orders
- Action buttons
```

#### Close Full Position
```
1. Click "Close" button on position
2. Confirm in dialog
3. Position closes at market price
4. All SL/TP orders cancelled
```

#### Partial Close
```
1. Click "Close %" button
2. Enter percentage to close (1-100)
3. Confirm in dialog
4. Specified quantity closes at market
5. Remaining position stays open
```

#### Update Trailing Stop Loss
```
1. Click "Trail SL" button
2. Enter trailing % (-1 to 0):
   - -0.3 = move SL 0.3% closer to current price
3. Confirm in dialog
4. Old SL orders cancelled, new SL placed
```

### Step 5: Monitor Performance

#### Trade History
```
Right panel → "📊 TRADE HISTORY"
Shows:
- Last 10 closed trades
- Time, Type (LONG/SHORT), Symbol
- Entry Price, Qty, Realized PnL
- Download as CSV
```

#### Live Trade Log
```
Right panel → "🔴 LIVE TRADE LOG"
Shows real-time events:
- ✅ Trade opens
- ❌ Trade closes
- ⚠️ SL updates
- Real timestamps

Click "Clear" to reset log
```

---

## Troubleshooting

### Error: "Order's notional must be no smaller than 5"
**Cause:** Qty × Entry Price < $5 minimum on Binance  
**Solution:**
1. Increase risk pool (deposit more USDT)
2. Widen SL distance (larger %)
3. Trade higher-priced symbols (BTC vs small alts)

### Error: "Qty override exceeds allowed size"
**Cause:** Manual qty > system recommended qty  
**Solution:**
1. Leave "Pos Override" empty (auto sizing)
2. Or widen your SL to allow larger position
3. Or reduce your leverage expectation

### Error: "Connection Failed - Please connect your exchange account"
**Cause:** Binance API not connected or invalid  
**Solution:**
1. Check API key has Futures enabled ✅
2. Verify API secret is correct
3. Re-connect: Settings → Exchange Connections
4. Check Binance account has USDT balance

### Position not updating
**Cause:** Cache or API lag  
**Solution:**
1. Manual refresh: Click position container
2. Wait 10 seconds (default cache duration)
3. Hard refresh browser: Ctrl+Shift+R

### SL/TP orders not placing
**Cause:** Invalid order parameters  
**Solution:**
1. Check TP1 > Entry for LONG (or TP1 < Entry for SHORT)
2. Check SL < Entry for LONG (or SL > Entry for SHORT)
3. Check order quantity > Binance minimum (see error message)

### Form warnings showing even with valid inputs
**Cause:** Client-side validation strictness  
**Solution:**
1. Ensure SL is truly valid for your side
2. Check all TP levels are in correct direction
3. Verify entry price > 0 for LIMIT orders
4. Wait for validation to complete (1 second)

---

## Configuration Files

### `config.py` - Trading Parameters
```python
MAX_RISK_PERCENT = 1           # 1% per trade
MAX_TRADES_PER_DAY = 5         # Daily limit
MAX_TRADES_PER_SYMBOL_PER_DAY = 2  # Symbol limit
SL_EDIT_MIN_PERCENT = -1       # Trailing SL bounds
SL_EDIT_MAX_PERCENT = 0        # Trailing SL bounds
```

### `logic.py` - Core Functions
- `execute_trade_action()` - Main trade execution
- `get_required_order_qty()` - Min notional validation
- `calculate_position_sizing()` - Auto sizing logic
- `get_open_positions()` - Live position fetch (cached)
- `get_trade_history()` - Trade log fetch (cached)

### `templates/index.html` - Frontend
- `confirmTradeExecution()` - Trade confirmation dialog
- `validateFormInputs()` - Real-time form validation
- `updateLivePositions()` - Position refresh (10s)
- `updateLivePrice()` - Price update (5s)
- `partialClose()` - Partial exit dialog
- `moveTrailingSL()` - SL adjustment dialog

---

## Performance Optimizations

✅ **API Call Reduction** (70% fewer calls)
- Live price: 5s interval (was 1s)
- Positions: 10s interval (was 3s)
- History: 30s interval (was 5s)

✅ **Client-Side Caching**
- Positions: 30s cache
- Trade history: 60s cache
- Live prices: 10s cache

✅ **Smart Cache Invalidation**
- Clears when trades execute
- Clears when positions close
- Clears on manual updates

---

## Support

For issues or features not listed:
1. Check the Live Trade Log for error messages
2. Review form validation warnings
3. Verify Binance API connection in Settings
4. Check browser console for JavaScript errors (F12)

---

**Happy Trading! 🚀**
