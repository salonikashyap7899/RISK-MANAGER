# Dashboard Real-Time & Precision Fixes
Status: 🚀 In Progress

## Issues Fixed:
- [x] Robust price fetching (never 0)
- [ ] Default symbol filters (precision logic)
- [ ] WebSocket real-time (100ms updates)
- [ ] Frontend safeguards
- [ ] Test Bybit integration

## Steps:
1. Fix logic.py#get_live_price() → Always return valid price
2. Add default filters to round_price/round_qty()
3. Add WebSocket endpoint
4. Update index.html JS
5. Test with Bybit
