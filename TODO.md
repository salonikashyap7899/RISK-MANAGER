# Debug Dashboard Redirect Issue

## Status: Analyzing

1. ✅ /debug-status + /debug-session created

2. [ ] Add logging to subscription_required decorator  
3. [ ] Fix admin user DB flag (is_admin=True)
4. [ ] Make API routes return JSON errors instead of HTML redirects
5. [ ] Test: Login as admin → dashboard → TP/SL action → check logs/console
6. [ ] Fix root cause based on logs

## Immediate Test
- Login admin
- Visit: http://localhost:5000/debug-status  
- Try TP/SL, check F12 Network tab (expect /api/place_trade → 200 JSON or error)

