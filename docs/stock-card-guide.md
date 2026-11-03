# Reading a stock card

Read the status first, then the quote time, price levels, two reasons and main risk. RESEARCH ONLY and WAIT do not indicate a confirmed buy. Reference targets are chart examples, not long-term valuations or predictions. Previous intraday briefs retain historical levels until a new brief is saved.

| Term | Plain meaning |
| --- | --- |
| Snapshot | The last fetched price. Its source and timestamp tell you whether it is current. Public data has unverified delay. |
| Entry above | Consider a breakout only after price moves above this level and the displayed conditions pass. |
| Stop loss / risk level | Planned exit or thesis failure level. A risk level on a research-only card is illustrative. Actual exit prices can slip. |
| Target | Reference exit price if the setup works; reaching it is uncertain. |
| Support / resistance | Areas of past buying / selling interest. Both can break. |
| Bullish / bearish | Rising / falling price trend under the app's moving-average rules. |
| RSI | Momentum from 0 to 100. The screenshot's 38.3 indicates weak momentum; it does not prove a bounce is coming. |
| Relative volume | Latest completed bar's volume divided by its preceding 20-bar average. 1.31 means about 31% more activity. It does not identify buyers versus sellers. |
| ATR | Typical true price range, including gaps. Used to set volatility-based distances. |
| VWAP | Average session price weighted by volume. Daily investment charts do not use it. |
| Swing structure | Comparison of confirmed highs and lows. Bearish structure suggests a weakening trend. |
| News tone | A keyword-based headline screen. Positive wording is evidence to check, not a return forecast. |
| Screen score | A weighted evidence checklist, not a probability of profit. Intraday/options use fewer components. |
| Reward / risk | Target gain divided by entry-to-stop distance. 0.5 means risking about Rs 2 for a possible Rs 1 gain, before costs. |
| Financials missing | Earnings, cash flow, debt and valuation have not been supplied and checked. Long-term research remains incomplete. |
| OI | Open option contracts. It cannot tell you whether buying or writing created those positions. |
| Put/call ratio | Put OI divided by call OI, for the returned expiry/strike window. It is not a standalone direction signal. |
| OI wall | Strike with the most returned put or call OI; a possible positioning area, not guaranteed support/resistance. |
| Data gaps | Missing or stale evidence that limits the recommendation. |

## The model numbers in your screenshot

- 43.0%: an uncalibrated model forecast of positive net return over 20 trading bars. It is not achieved trading accuracy.
- 64.3%: correct classifications on unseen historical test observations.
- 61.7%: accuracy of the simple majority-class baseline on those observations. The model's advantage is only 2.6 percentage points on this test.
- 1 simulated trade: too little trade evidence to judge reliability. Its mean net return was -2.939% under the simulation's assumptions.
- Drawdown: the largest simulated equity decline from a prior peak.
- Train/test counts: historical observations used to fit and check the classifier. They are not a count of profitable trades.
- 20 bps: assumed round-trip cost of 0.20%. Actual costs and execution can differ.
- Brier score: a probability-error measure; lower is better. It and training diagnostics remain in exports because they are unnecessary for routine card reading.

The stock screenshot combines falling prices, weak momentum, positive headlines and missing financials. That is mixed evidence for research, not a strong verified buy.

## Additions now available

The five additions below are now implemented in Trading desk and new research reports. See [the trading-desk guide](trading-desk-guide.md) for scope and assumptions. Financial reviews are source-linked, not audited; missing metrics stay partial.

1. Paper-trading journal and walk-forward evaluation: log every daily idea, entry/exit, fees, slippage, net return and drawdown; compare against a baseline before using real capital.
2. Position and portfolio risk controls: budget-based sizing, total open risk, sector concentration and a daily loss limit.
3. Opening confirmation and alerts: fresh Kite quotes, opening range, volume, spread and gap checks; invalidate old ideas when conditions fail.
4. Market/sector strength and event calendar: distinguish trending and sideways sessions; flag results, major announcements and expiry risk.
5. Verified financials and valuation: financial statements, cash flow, debt, peer valuation and portfolio monitoring for long-term ideas.

These aim to improve decision quality and measurement. They cannot ensure a profit every day. Kite provides broker data; separate news and company financial sources remain necessary. Its access token normally expires at 6 AM, which matters for an authenticated 7 AM brief.

Sources: [Kite features and pricing](https://zerodha.com/products/api/), [quote fields](https://kite.trade/docs/connect/v3/market-quotes/), [token lifecycle](https://kite.trade/docs/connect/v3/user/).
