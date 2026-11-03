# ConsentDesk — features implemented, 31 October 2026

- **Sector top three:** choose Healthcare, Energy & Power, Railways, Technology, Banking & Finance, Automobiles, Consumer & Retail, Metals & Mining, Defence or Infrastructure & Realty. Each has separate intraday/long-term research from a curated six-stock universe. Previous briefs remain until replacement succeeds.
- **Concise proof:** price/source/time, entry/stop/target references, short reasons, chart patterns, source links and primary risk. Expand opposing evidence and missing checks.
- **Research brain:** completed chart patterns and daily/intraday alignment; momentum, volume, VWAP; recent news catalysts/provider summaries, contradictions; market regime, financial quality, valuation and OI context. Missing or conflicting evidence can produce WAIT. Optional AI explains supplied evidence; news/OI are not trained forecasting features.
- **Live data:** free Upstox Analytics Token integration, one shared V3 streaming connection, reconnect/freshness checks and ten-second snapshot fallback. User token required; live authenticated verification is pending.
- **Daily schedule:** 07:00 IST weekday scans while host runs, queued by sector/mode. Dates and retained previous lists are visible. Jobs persist across restart.
- **Paper desk:** persistent cash-stock simulations, cost-aware share sizing, sector/daily/open-risk limits, net outcomes and CSV export. Setup alerts recheck current news/daily trend after technical confirmation. No real orders.
- **Public beta support:** accounts, hashed passwords, isolated private histories/ledgers/alerts, CSRF, quotas, bounded connections and global AI budget. Common daily research stays shared. Private mode remains available.
- **Operations:** HTTPS/Docker templates, persistent storage, health/status endpoints and SQLite backup utility.

**Verified:** 92 Python tests and both Node suites pass; a real Healthcare scan returns three research-only ideas with linked news. Compose configuration validates. Browser visuals could not be verified after computer-use stopped; Docker engine was unavailable for a runtime deployment check. No profitability or 90% reliability claim is made.

**External steps left:** generate/configure Upstox token; verify during market hours; provide hosting/domain; resolve public data-display and recommendation permissions before public release. Email verification/recovery and large-scale multi-instance operation are outside this beta. No public deployment performed.

[Setup and deployment guide](release-and-deployment.md).
