# FYERS setup

Direct developer dashboard: https://myapi.fyers.in/dashboard/

Create an API app in your active FYERS account. Keep the App ID (including -100), secret and exact registered HTTPS redirect URL. Enable market data/history and Transactions Info for market-status checks. Order placement is not needed.

Run `python scripts/connect_fyers.py` locally. Enter your App ID, secret (hidden) and registered redirect URL. Open the official login URL it prints, sign in yourself, then paste the FULL redirected URL into the hidden prompt. The helper checks OAuth state and saves the App ID/access token in server .env. Your secret is not saved or printed. Restart with `.\start.ps1`.

Configuration: MARKET_BROKER=fyers, FYERS_APP_ID=your_app_id, FYERS_ACCESS_TOKEN=your_access_token. The local provider selection has been changed; credentials are still blank.

FYERS tokens are daily: repeat the helper when expired, then restart. The 7 AM broker scan needs valid credentials. App ID alone is insufficient.

Supported: 10-second authenticated price snapshots, candle charts, historical validation and NSE normal-session verification. Missing exchange timestamps never claim live status. News uses existing RSS; financials can be entered from sources. FYERS streaming and option-chain adaptation are not implemented. Upstox features remain available when separately configured.

Authenticated verification needs your credentials. No orders are placed. Public display permissions are separate from free API access.
