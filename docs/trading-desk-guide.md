Updated release: sector top-three dropdowns, shared live streaming and public-beta accounts are now available. See [31 October release guide](release-and-deployment.md). The earlier snapshot-only and common-workspace descriptions below apply to the original private release.

# Trading desk additions — 10 October 2026

Open **Trading desk** in the sidebar. It shares the common workspace.

| Feature | What it does |
| --- | --- |
| Paper journal | Simulated cash-stock entries/exits; net P&L, win rate, fees, slippage and closed-trade drawdown. CSV exports the latest 100 displayed closed trades. |
| Position sizing | Shares within cash, per-trade risk, total stop risk, daily loss/risk budget and sector cap. Unknown sectors share a cap. |
| Card shortcuts | **Paper plan** copies levels into sizing. **Watch entry** links an eligible current intraday card to an alert. Old setups cannot arm alerts. |
| In-app alerts | Price above/below a level or saved-setup confirmation: opening range, completed breakout, volume, VWAP, spread and gaps. Fresh broker data and a verified normal session required. Expired or breached setups stop watching. |
| Events | Source-linked earnings/policy/expiry/closure dates; Upstox corporate-action fetch. Same-day high-impact events hold intraday setups for review. News flags do not invent future dates. |
| Market context | Completed-daily Nifty regime, aligned 20-bar return versus Nifty, and up to two same-sector peers in new reports. Falling Nifty regime holds long intraday setups for review. Peer averages are not sector indices. |
| Historical testing | Three expanding windows, purged boundaries, train-only normalization. Accuracy versus baseline, net win rate and interval, net return, drawdown and higher-cost stress. Saved across restarts. |
| Financial review | Upstox statements/ratios or manual source-linked metrics; company/sector P/E. Missing debt and inconsistent periods keep reviews partial. Liabilities and share capital are not substitutes for debt and total equity. |

**Paper fills:** manual prices are simulated references. Automatic stop/target/session exits use sampled fresh broker prices plus configured costs. Offline intraday positions have no invented next-day fill. Alerts and paper exits check every 10 seconds while the host runs; crossings between checks may be missed. No real orders, push messages or options simulations.

**Risk numbers:** stop risk includes estimated fees/slippage. Daily room conservatively subtracts current losses and reserves open stop risk. Stops can gap. Sector exposure uses entry capital. Starting capital locks after the first paper record; existing trades retain original cost assumptions. Drawdown covers closed-trade results, not intratrade losses.

**Verification:** 74 Python regression tests and frontend daily-retention and paper-form/CSV checks pass. Checks cover concurrent sizing, restart persistence, costs, stale-data gates, expiry, sampled exits, financial parsing and leakage. Broker endpoints use documented fixtures; authenticated live verification needs your token. An actual Infosys daily-history test returned 51.0% direction accuracy versus 47.9% baseline, with only three simulated trades: insufficient evidence of trading reliability. Tests do not validate news/OI, realistic stop fills or future profit. No 90% reliability claim is made.

## Free live NSE data

**Upstox Analytics Token is supported.** It is free, read-only and valid for one year. Generate it in your own Developer Apps account under Analytics. Set these server-side values in `.env`, then restart:

```dotenv
MARKET_BROKER=upstox
UPSTOX_ANALYTICS_TOKEN=your_token_here
```

Supports quotes, history, options, fundamentals, corporate actions and streaming without a static IP for those endpoints. This app uses **10-second snapshots**, not WebSocket tick streaming. You can use Upstox for data while trading manually in Zerodha. [Official token documentation and setup](https://upstox.com/developer/api-documentation/analytics-token/).

**FYERS API V3** also offers free live WebSocket and historical data with a FYERS account/API app. It is an alternative; a FYERS adapter is not implemented here. [Official zero-cost data notice](https://fyers.in/notice-board/discontinuation-of-truedata-support-from-26th-december-2025).

**Zerodha:** free Personal API excludes market data; Kite Connect market-data access costs ₹500/month. The Kite adapter remains available. [Official pricing](https://zerodha.com/products/api/). Yahoo fallback is unofficial and delay unverified. Check redistribution permissions before publicly displaying broker data.

Keep the host running for the 07:00 IST weekday brief and monitor. Last completed brief stays until its replacement is saved. No credentials or hosted deployment have been supplied.
