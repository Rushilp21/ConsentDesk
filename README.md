# ConsentDesk

### Indian stock research, market evidence, and paper trading in one workspace.

ConsentDesk is a web dashboard and research chatbot for exploring Indian equities. It combines broker prices, company news, chart patterns, market context and financial evidence to produce concise intraday and long-term research ideas. Every idea includes its supporting evidence, risks and data limitations.

The project supports private use and an optional public beta mode with separate user accounts. **Real-money order execution is disabled.** Research scores and experimental forecasts are not guarantees of profit.

## Features

### Market dashboard

- NSE symbol search, timestamped prices and intraday/daily candlestick charts.
- Upstox V3 streaming with a shared backend connection, reconnection and snapshot fallback.
- Support/resistance, RSI, ATR, VWAP, relative volume and confirmed geometric patterns.
- Market-session checks, freshness labels and explicit missing-data states.

### Recommendations and chatbot

- Separate intraday and long-term daily briefs, scheduled for **07:00 IST on weekdays** while the backend runs.
- Sector/theme dropdowns with **three ranked research ideas** per category: Healthcare, Energy & Power, Railways, Technology, Banking & Finance, Automobiles, Consumer & Retail, Metals & Mining, Defence, and Infrastructure & Realty.
- Each category screens a curated six-stock universe; this is bounded coverage rather than an exhaustive NSE ranking.
- Short reasons, patterns, linked news evidence, reference/conditional price levels and the main risk. Additional evidence expands on demand.
- Previous briefs remain visible until a replacement is successfully saved.
- Research stages cover data collection, chart analysis, news catalysts, contradictory evidence, market/event checks and synthesis.
- Optional AI planning and grounded explanations; deterministic research remains available without an AI key.

### Evidence and historical testing

- Recent headlines and available provider summaries, with source links and publication dates.
- Daily/intraday trend alignment, Nifty regime, relative strength and limited sector-peer comparisons.
- Source-linked financial inputs and Upstox financial review, including company/sector valuation comparisons where available.
- Options OI, put/call ratios and positioning research through supported broker adapters.
- Experimental chart classifier, chronological holdout and three-window walk-forward testing with leakage controls, baseline comparison, costs and uncertainty.

### Paper trading and accounts

- Persistent simulated entries/exits, estimated net P&L, fees, slippage and CSV export.
- Position sizing constrained by available cash, per-trade risk, open risk, sector exposure and daily loss/risk budget.
- In-app price/setup alerts, breakout confirmation and source-linked event calendar.
- Optional public beta accounts with hashed passwords, cookie sessions, CSRF checks and isolated histories, paper portfolios and alerts.
- Bounded research concurrency, request allowances, AI-call limits, job persistence and SQLite backups.

## Technology

| Component | Implementation |
| --- | --- |
| Frontend | HTML, CSS and vanilla JavaScript |
| Backend | Python 3.11+ HTTP service |
| Persistence | SQLite on a persistent backend disk |
| Live feed | Upstox V3 WebSocket; `websocket-client` |
| Analysis | Reproducible evidence checks and experimental price-pattern classifier |
| Optional language model | OpenAI Responses API |
| Deployment | Docker/HTTPS backend; optional Vercel frontend |

## Quick start

```sh
git clone https://github.com/Rushilp21/ConsentDesk.git
cd ConsentDesk
python -m pip install -r requirements.txt
```

Create your local configuration:

```powershell
# Windows PowerShell; copy only if .env does not already exist
Copy-Item .env.example .env
```

On macOS/Linux, use `cp .env.example .env` instead. Add your own credentials to `.env`, then start:

```sh
python server.py
```

Open **http://127.0.0.1:8000/**. Windows users can also run `.\start.ps1`.

Public fallback research is available without broker keys, but its price delay is unverified. Demo data is explicitly labelled. Valid broker credentials are required for authenticated prices and live-data alerts.

## Configuration

`.env.example` contains the available settings. **Never commit `.env`, access tokens or API secrets.**

| Setting | Purpose |
| --- | --- |
| `MARKET_BROKER` | `upstox`, `fyers` or `zerodha` |
| `UPSTOX_ANALYTICS_TOKEN` | Upstox read-only data access |
| `MARKET_STREAM_ENABLED` | Enable the Upstox streaming connection |
| `FYERS_APP_ID`, `FYERS_ACCESS_TOKEN` | FYERS authenticated data access |
| `KITE_API_KEY`, `KITE_ACCESS_TOKEN` | Zerodha Kite data access |
| `OPENAI_API_KEY`, `OPENAI_MODEL` | Optional AI planner/explainer |
| `DAILY_SCAN_TIME` | Daily schedule in IST; default `07:00` |
| `DATA_DIR` | Persistent storage directory |
| `PUBLIC_MODE` | Enable isolated public-beta accounts |
| `PUBLIC_ORIGIN` | Exact HTTPS frontend origin for a separately hosted frontend |
| `DAILY_RESEARCH_LIMIT`, `AI_DAILY_CALL_LIMIT` | Usage controls |

### Broker capabilities

| Provider | Implemented support | Setup |
| --- | --- | --- |
| Upstox | Authenticated quotes, V3 streaming, history, news, options and financial review | [Analytics Token documentation](https://upstox.com/developer/api-documentation/analytics-token/) |
| Zerodha Kite | Quote snapshots, history and listed options/OI | [Official API information](https://zerodha.com/products/api/) |
| FYERS | Ten-second quote snapshots, history and NSE session checks; FYERS streaming/options adapters are not implemented | [Local setup guide](docs/fyers-setup.md) |

The Upstox Analytics Token is read-only. FYERS login assistance is available through `python scripts/connect_fyers.py`; run it locally and follow the prompts. Renew expired credentials according to the provider's requirements, then restart the backend.

## Project structure

```text
ConsentDesk/
|-- consentdesk/             # Research, providers, analysis, accounts and persistence
|   |-- agent.py             # Research orchestration
|   |-- daily.py             # Scheduled and sector-specific briefs
|   |-- reasoning.py         # Evidence synthesis and contradictions
|   |-- sectors.py           # Curated category membership
|   |-- providers.py         # Broker/public data routing
|   |-- streaming.py         # Shared Upstox V3 feed
|   |-- learning.py          # Experimental classifier and validation
|   |-- desk.py              # Paper ledger, sizing and alerts
|   |-- accounts.py          # Accounts, sessions and allowances
|   `-- storage.py           # SQLite reports and jobs
|-- web/                     # Dashboard, chatbot and trading-desk UI
|-- scripts/                 # Login helper, training, backups and packaging
|-- tests/                   # Python and JavaScript regression suites
|-- docs/                    # Feature, training and deployment guides
|-- examples/                # Sample financial-data template
|-- deployment/vercel/       # Generated static frontend and API rewrite configuration
|-- server.py                # Local/backend entry point
|-- requirements.txt         # Runtime dependency
|-- .env.example             # Credential-free configuration template
|-- Dockerfile               # Backend container
|-- compose.yaml             # Local/persistent backend setup
|-- compose.public.yaml      # Public HTTPS overlay
`-- Caddyfile                # HTTPS reverse-proxy configuration
```

Local `.env`, databases, backups, temporary files, bytecode and generated release outputs are excluded from version control.

## Deployment

### Persistent backend

The backend needs an always-running host and persistent storage for accounts, saved research, jobs and paper trades. Docker configuration is included:

```sh
docker compose up -d --build
```

For public HTTPS hosting, follow the [release and deployment guide](docs/release-and-deployment.md). Confirm market-data display rights and applicable requirements for public stock recommendations before enabling public mode. Registration, email recovery and large-scale operation require further operational planning; this release is intended for a small beta.

### Vercel frontend

The supplied deployment arrangement places the frontend on Vercel and keeps the Python backend on a separate persistent host:

```sh
python scripts/prepare_vercel.py --backend https://YOUR-BACKEND-HOST
```

Deploy the generated `deployment/vercel` directory. Set the backend's `PUBLIC_ORIGIN` to the exact frontend HTTPS origin. **The checked-in example points to `backend.example.invalid`; regenerate it before deployment.**

This is not a Vercel-only backend migration. Do not substitute temporary function storage for the persistent database. UI changes require regenerating and redeploying the frontend; backend changes deploy to the backend host.

See [Vercel deployment instructions](docs/vercel-deployment.md).

## Verification

```sh
python -m unittest discover -s tests -v
node tests/test_brief_ui.js
node tests/test_desk_ui.js
```

The latest verified release passed **98 Python tests and both JavaScript suites**. Read-only Upstox checks also confirmed authenticated quotes, streaming packets, news, history and option-chain access. These checks verify software/data access, not profitable trading. Browser visual verification and cloud deployment/runtime checks remain incomplete; market-hours behaviour should be verified before relying on alerts.

Optional provider smoke check:

```sh
python scripts/check_live_data.py
```

Database backup:

```sh
python scripts/backup.py --destination backups
```

Keep backups private and test restoration. The host must remain running for the schedule, streaming and in-app alerts.

## Documentation

- [Implemented features](docs/implemented-features.md)
- [Stock-card meanings](docs/stock-card-guide.md)
- [Trading-desk guide](docs/trading-desk-guide.md)
- [Training and evaluation](docs/training-guide.md)
- [Release and backend deployment](docs/release-and-deployment.md)
- [Vercel frontend deployment](docs/vercel-deployment.md)
- [FYERS setup](docs/fyers-setup.md)

## Scope and limitations

- Research rankings are evidence screens, not predictions of the most profitable stocks.
- Model probabilities are experimental and uncalibrated. News and OI are research inputs, not trained forecasting features.
- Headlines/provider summaries and financial inputs need source verification. Missing evidence stays visible.
- Paper fills use assumptions and sampled prices; they do not reproduce every real execution or gap.
- Public access to a broker API does not itself grant permission to redistribute data.
- No real-money orders are submitted. No daily-profit or 90% trading-reliability claim is made.
