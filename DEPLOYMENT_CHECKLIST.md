# Deployment Checklist for Fixing Internal Server Error

## Files Modified:
1. ✅ app.py - Added error handlers and debug route
2. ✅ logic.py - Improved Binance client error handling

## Required Environment Variables:
Make sure these are set in your production environment:

```env
# Required for production:
SECRET_KEY=<generate-a-secure-random-key>
DATABASE_URL=<your-postgres-database-url>

# Optional - for default Binance client:
BINANCE_KEY=<your-binance-api-key>
BINANCE_SECRET=<your-binance-secret>

# Razorpay (required for subscriptions):
RAZORPAY_KEY_ID=<your-razorpay-key-id>
RAZORPAY_KEY_SECRET=<your-razorpay-key-secret>
RAZORPAY_MONTHLY_PLAN_ID=<your-plan-id>
RAZORPAY_YEARLY_PLAN_ID=<your-plan-id>

# Google OAuth (optional):
GOOGLE_CLIENT_ID=<your-google-client-id>
GOOGLE_CLIENT_SECRET=<your-google-client-secret>
```

## After Deployment:

1. Visit `/debug-status` to verify the server is working correctly
2. Check that the response shows:
   - `"status": "ok"`
   - `"database": "connected"`

3. Test the main routes:
   - `/` (home)
   - `/login`
   - `/register`

## Common Issues:

### If you still get Internal Server Error:
- Check your production logs (e.g., `heroku logs` or your hosting provider's logs)
- The new error handlers should now show the home page instead of a blank error
- Visit `/debug-status` to see what's configured

### Database Issues:
- Make sure DATABASE_URL is properly set
- Run database migrations if needed

### Razorpay Issues:
- Verify RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET are correct
- Check that plan IDs are valid Razorpay plan IDs

