# Binance Geo-Restriction Fix - Implementation Plan
Status: ✅ In Progress

## Steps:
- [x] 1. Create TODO.md ✅
- [x] 2. Update logic.py with proxy support & geo-error handling ✅
- [ ] 3. Add /test-binance route to app.py + PROXY_URL in config.py
- [ ] 4. Test VPN connectivity: `python -c "import requests; print(requests.get('https://fapi.binance.com/fapi/v1/time').json())"`
- [ ] 5. Restart app (`python app.py`), test /debug-status & /test-binance
- [ ] 6. Mark complete

**Next:** config.py & app.py updates.

**Primary Fix:** Use VPN (US/Singapore server). Proxy support added via config.PROXY_URL (e.g., 'http://proxy-server:port').

