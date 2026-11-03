# Sector recommendations and live-data release — 31 October 2026

## Implemented

- **Sector dropdowns:** dashboard and chatbot daily brief support Healthcare, Energy & Power, Railways, Technology, Banking & Finance, Automobiles, Consumer & Retail, Metals & Mining, Defence, and Infrastructure & Realty. Each scans six explicitly classified stocks and saves its own top three for intraday/long term. Railways/Defence are themes. This is bounded coverage, not a complete NSE sector index.
- **Short evidence cards:** pattern names, available/supporting checks, source links, entry/stop/target references and primary risk. Expand “Why / why not” for trend, RSI, relative volume, news catalysts, missing financials, opposing evidence and OI context. A checklist count is not a profit probability.
- **Deeper analysis:** compares daily and intraday trends, completed chart patterns, volume/VWAP, fresh news catalysts, conflicting headlines, market regime and sourced events. Broker news includes summaries when available; RSS is a fallback. Full articles are not independently verified. Conflicting evidence can hold a setup at WAIT. News/OI are research inputs, not trained prediction features.
- **Free-provider integration:** Upstox Analytics Token is the default. One shared V3 WebSocket full-feed connection serves watched symbols; the UI checks its latest cache every second. REST snapshots refresh every ten seconds as fallback. Reconnects use bounded backoff; initial/stale/future/disconnected packets do not claim live status. One connection is capped at 200 watched instruments and idle subscriptions lapse. Chart refresh remains every minute. Streaming depends on your token, network and market hours.
- **Usability:** 07:00 IST weekday scheduler generates sector/mode briefs over successive worker slots. Previous lists remain until replacements succeed. Sector changes do not mix asynchronous responses. Completed jobs survive restart; interrupted jobs become retryable errors. In-app setup alerts recheck fresh news and daily trend after technical confirmation.
- **Public beta mode:** hashed passwords, opaque HttpOnly sessions, CSRF checks, per-account SQLite workspaces, isolated research/paper trades/alerts, request limits, daily research allowance, shared three-job worker cap, and a persistent global AI-call budget. Old personal reports are preserved in the local workspace and are not imported into new public accounts. Shared daily briefs remain common research.
- **Operations:** bounded backend connections, Docker configuration, HTTPS reverse-proxy template, health endpoint, status view and consistent SQLite backup utility. Public registration is capped at 100 accounts by default. Email verification, email password recovery, billing and large-scale multi-instance operation are not implemented.

## Start locally

```powershell
cd 'D:\Trading Agent'
python -m pip install -r requirements.txt
.\start.ps1
```

Private mode remains the default. A local `.env` has been created from the template if none existed. No credential was generated or supplied.

Generate your own free Analytics Token under Analytics in [Upstox Developer Apps](https://account.upstox.com/developer/apps), then edit `.env`:

```dotenv
MARKET_BROKER=upstox
MARKET_STREAM_ENABLED=true
UPSTOX_ANALYTICS_TOKEN=your_token_here
PUBLIC_MODE=false
```

Restart. Open the dashboard's **Live data setup & system status**. During a normal market session, watch for CONNECTED and LIVE STREAM/current timestamps; test a deliberately expired token and recovery before real use. Read-only token setup: [official documentation](https://upstox.com/developer/api-documentation/analytics-token/). Token validity is one year. Generating an account/token is a user action; it is not replaced by a public anonymous live-data feed.

The optional language-model key/model fields enable richer grounded explanations. Without them, the reproducible evidence analysis and historical chart learner still work. The learner's forecasts remain experimental and uncalibrated; neither “deep” analysis nor tests establish profitable trading or 90% reliability.

## Public deployment

1. Confirm the right to display your provider's market data to visitors, and review applicable requirements for public stock-specific recommendations. Free personal API access does not grant public redistribution rights. See [NSE data policy](https://www.nseindia.com/static/market-data/nse-data-policy). Calling the app research-only does not resolve its legal classification.
2. Provision one always-running Docker host and point a domain to it. Use a persistent disk; this release uses one backend instance and local SQLite. Do not replicate workers/schedulers across multiple instances.
3. Copy source and `.env` securely. Set `DOMAIN=your.actual.domain`, `PUBLIC_DATA_APPROVED=true` **only after step 1**, and keep `SECURE_COOKIES=true`. The public Compose overlay enables public mode and trusted proxy forwarding. The backend remains bound to localhost; only the proxy exposes 80/443.
4. Validate and launch:

   ```sh
   docker compose -f compose.yaml -f compose.public.yaml config --quiet
   docker compose -f compose.yaml -f compose.public.yaml up -d --build
   ```

5. Verify HTTPS, registration/login/logout, two-user isolation, current market data, 07:00 IST scans, token expiry/reconnection and backup restoration on the actual server. Adjust `MAX_USERS`, `DAILY_RESEARCH_LIMIT` and `AI_DAILY_CALL_LIMIT` to your beta budget. Set `ALLOW_REGISTRATION=false` when enrolment is closed. Keep one shared quote connection; do not distribute the broker token to browsers.
6. Schedule a nightly backup with your hosting scheduler:

   ```sh
   docker compose -f compose.yaml -f compose.public.yaml exec -T consentdesk python scripts/backup.py --destination /app/backups
   ```

   Backups contain accounts and personal journals. Store them privately and copy them off the host. Each file is a consistent SQLite snapshot; the collection is not an atomic cross-database snapshot. Restore into a separate stopped test instance first.

## Verification and remaining external steps

**92 Python tests and both Node frontend suites pass.** Tests cover sector membership/top-three selection, source/missing-evidence handling, cross-sector response races, account boundaries, CSRF, costs/sizing, live-feed protobuf decoding, binary subscriptions, freshness gates, budgets, restart recovery and backups. A real Healthcare scan completed using public news/history and saved three research-only cards with sources.

The public Compose configuration validates with a placeholder domain. Docker's Linux engine was not running, so an image build/proxy runtime test was not performed. Browser visual verification stopped because the computer-use tool could not reliably determine the active URL; no further browser actions were taken. Upstox-authenticated streaming and language-model calls remain unverified without credentials. No public deployment was performed. Your token, hosting/domain and public-display/recommendation permissions remain external setup steps. Real-money order execution is disabled.
