# Risk Manager Pro - Enhanced Trading Platform

A professional-grade Binance Futures trading platform with advanced risk management, live P&L tracking, and comprehensive trade analytics.

## 🎯 Features

### ✅ UPGRADE #1: Enhanced Live Position Display (Binance-Style)
- **Real-time P&L Tracking**: Live unrealized PNL with color-coded display
- **ROI Percentage**: Instant ROI calculation based on margin used
- **Position Size**: Total notional value in USDT
- **Margin Details**: Allocated margin and margin ratio
- **Price Metrics**: Entry price, mark price, and liquidation price
- **Risk Indicators**: High margin ratio warnings
- **Auto-refresh**: Updates every 8 seconds

### ✅ UPGRADE #2: Fixed TP/SL Order Execution
- **STOP_MARKET Orders**: Proper futures stop-loss orders
- **TAKE_PROFIT_MARKET Orders**: Working take-profit orders
- **Partial TP Support**: TP1 with percentage, TP2 for remaining
- **closePosition Flag**: Ensures full position closure on SL/TP2
- **Better Error Handling**: Detailed logging and error messages

### ✅ UPGRADE #3: CSV Trade History Export
- **Complete Trade Log**: Download all executed trades
- **Detailed Metrics**: Time, symbol, side, entry, SL, TP levels, leverage
- **Timestamped Files**: Each export has unique timestamp
- **One-Click Download**: Simple button in UI

### 🔧 Core Features (Preserved)
- **1% Risk Model**: Automatic position sizing based on unutilized capital
- **Leverage Calculator**: 100 / (SL% + 0.2) formula
- **Daily Trade Limit**: 4 trades per day protection
- **Multiple Symbols**: Support for all Binance USDT futures pairs
- **Isolated/Cross Margin**: Choose your margin mode
- **TradingView Charts**: Integrated live charts
- **Real-time Prices**: Auto-updating market prices

## 📋 Requirements

- Python 3.8+
- Binance Futures account with API keys
- Active internet connection

## 🚀 Installation

1. **Install dependencies**:
```bash
pip install -r requirements.txt
```

2. **Configure API keys**:
Edit `config.py` and add your Binance API credentials:
```python
BINANCE_KEY = "your_binance_api_key_here"
BINANCE_SECRET = "your_binance_api_secret_here"
```

**Getting API Keys:**
- Visit: https://www.binance.com/en/my/settings/api-management
- Create new API key
- Enable "Futures" trading permissions
- Add IP whitelist for security (recommended)

3. **Run the application**:
```bash
python app.py
```

4. **Access the platform**:
Open your browser to: `http://localhost:5000`

## 🎮 Usage Guide

### Placing a Trade

1. **Select Symbol**: Choose your trading pair (e.g., BTCUSDT)
2. **Choose Direction**: Click LONG or SHORT
3. **Set Entry**: Market orders use live price automatically
4. **Configure Stop Loss** (MANDATORY):
   - Choose "SL % Movement" or "SL Points"
   - Enter value (e.g., 2% or specific price)
5. **Optional Take Profits**:
   - TP1: Price and percentage of position
   - TP2: Final exit price
6. **Override (Optional)**:
   - Position size (suggested value shown)
   - Leverage (calculated automatically)
7. **Execute**: Click "EXECUTE EXCHANGE ORDER"

### Monitoring Positions

The **OPEN POSITIONS** panel shows all active trades with:
- Live unrealized P&L (green = profit, red = loss)
- ROI percentage
- Position details (size, margin, prices)
- Margin ratio warnings
- One-click close button

### Downloading Trade History

Click the **📥 CSV** button next to "TRADE LOG" to download complete trading history with all details.

## 🔒 Security Best Practices

1. **API Restrictions**:
   - Enable only "Futures Trading" permission
   - Never enable "Withdrawal" permission
   - Use IP whitelist

2. **Key Storage**:
   - Never commit config.py to version control
   - Use environment variables in production
   - Rotate keys regularly

3. **Risk Management**:
   - Never disable the daily trade limit
   - Always set stop losses
   - Start with small position sizes

## 🐛 Troubleshooting

### "Binance client not connected"
- Check your API keys in config.py
- Verify API key has Futures trading enabled
- Check your internet connection

### "TP/SL orders not placing"
- Ensure TP/SL prices are valid (TP above entry for LONG, below for SHORT)
- Check you have sufficient margin
- Verify the symbol supports STOP_MARKET and TAKE_PROFIT_MARKET orders

### "Position not showing in Live P&L"
- Wait 8 seconds for auto-refresh
- Check if position was actually filled on Binance
- Verify you're looking at Futures positions (not Spot)

### "CSV download empty"
- Make sure you've executed at least one trade
- Trade history is session-based (clears on browser close)

## 📊 Technical Details

### Position Sizing Formula
```
Risk Amount = Unutilized Capital × 1%
Max Leverage = 100 / (SL% + 0.2)
Position Size = (Risk Amount / (SL% + 0.2)) × 100
```

### Order Types Used
- **Entry**: MARKET orders
- **Stop Loss**: STOP_MARKET with closePosition
- **Take Profit 1**: TAKE_PROFIT_MARKET with specific quantity
- **Take Profit 2**: TAKE_PROFIT_MARKET with closePosition

### API Rate Limits
- Symbol cache: 1 hour
- Price cache: 5 seconds
- Position updates: 8 seconds (client-side)

## 📁 Project Structure

```
/app/
├── app.py              # Flask application
├── logic.py            # Trading logic & Binance API
├── config.py           # API configuration
├── requirements.txt    # Python dependencies
├── templates/
│   └── index.html      # Enhanced UI with Binance-style display
└── static/
    └── style.css       # Styling
```

## 🔄 What's New in This Version

### Fixed Issues:
1. ✅ TP/SL orders now execute properly using STOP_MARKET and TAKE_PROFIT_MARKET
2. ✅ closePosition flag ensures full position closure
3. ✅ Better error handling and logging

### New Features:
1. ✅ Complete Binance-style position display with ROI, margin ratio, etc.
2. ✅ CSV export for complete trade history
3. ✅ Enhanced UI with better visual feedback
4. ✅ Improved caching to avoid rate limits

## ⚠️ Disclaimer

This software is for educational purposes only. Trading cryptocurrencies involves substantial risk of loss. The developers are not responsible for any trading losses incurred while using this platform.

Always:
- Test with small amounts first
- Understand the risks
- Never trade with money you can't afford to lose
- Do your own research (DYOR)

## 📞 Support

For issues or questions:
1. Check the Troubleshooting section
2. Review Binance API documentation: https://binance-docs.github.io/apidocs/futures/en/
3. Verify your API key settings on Binance

## 📝 License

MIT License - Feel free to modify and use as needed.

---

**Happy Trading! 🚀**
