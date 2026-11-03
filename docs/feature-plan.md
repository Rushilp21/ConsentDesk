# ConsentDesk implementation plan

Build a common Indian-market dashboard and research chatbot focused on news, chart evidence and recommendations. Profitability is a measured goal, not an assumed property.

## Features
- Market dashboard: NSE stock search, current quote snapshots every 10 seconds, source timestamps and fresh-versus-delayed labels. Intraday/daily candlestick charts refresh every minute.
- Research chatbot: intraday, options and long-term modes; watchlists, budget/risk controls, follow-up questions, shared saved reports and evidence exports.
- Agent workflow: scope planner, concurrent market/news tools, indicator analyst, pattern learner, risk checks and grounded synthesis. Optional AI explains structured evidence; deterministic research works without an AI key.
- News: company RSS headlines with dates, publisher links, deduplication, freshness filters and disclosed sentiment. External content is evidence, never an instruction.
- Charts: trend, RSI, ATR, VWAP, volume, confirmed geometric patterns and observed support/resistance. Experimental statistical learning uses actual historical bars, a purged chronological holdout and measured baseline comparisons.
- Daily research: persistent weekday intraday and investment shortlists over configurable symbols while the server runs. Entry, target and invalidation are conditional scenarios; stale or missing evidence produces WAIT.
- Zerodha Kite integration: read-only quote snapshots, historical candles and listed option contracts. Per-expiry OI, put/call ratios and concentrations disclose partial-chain coverage. No invented Greeks or historical OI.
- Long-term review: daily price/news evidence and optional sourced financial quality inputs. Business quality and valuation require further review.

## Validation and deployment
Use a standard-library Python backend and responsive browser frontend with startup scripts, Docker files and meaningful tests. Verify data freshness, risk bounds, shared history, provider failures and training leakage. Synthetic demonstration is visibly separated from real research. Live broker calls need credentials and data entitlement. Deploy privately; no hosted deployment is performed here.

## Training and transactions later
The learner remains experimental until stronger walk-forward evaluation, execution-cost simulation, calibration and prospective paper trading. No training recipe guarantees 90% profitable trades. Historical news and OI require aligned datasets before training.

A disabled typed execution boundary allows a later confirmed-order phase with broker login, paper fills, previews, instrument/lot checks, idempotency and reconciliation. Recommendation text alone must not place orders.
