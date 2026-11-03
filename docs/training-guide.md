# Training and measuring the trading model

The implemented chart learner is an experimental logistic-regression model using 10 OHLCV features. The chatbot explains tool evidence. Training an LLM to sound confident does not make trading profitable. No model in this project has achieved or been certified at 90% trade reliability.

## Run the implemented learner

Research a stock in the chatbot to train automatically, or run:

```powershell
python scripts/train_patterns.py --symbol INFY --mode longterm
python scripts/train_patterns.py --symbol INFY --mode intraday --csv data/history/INFY-5minute.csv
```

CSV columns: `timestamp,open,high,low,close,volume`. Use epoch seconds or ISO timestamps with timezone, completed bars, consistent five-minute spacing for intraday, and daily trading bars for long term. Check splits, dividends, duplicates and missing sessions. Do not insert fake rows. Public raw prices may contain corporate-action artifacts. Five years of public daily history or broker history is fetched when available; neither is a validated training dataset by default.

The model learns the binary outcome of a positive close-to-entry return after an assumed 20-basis-point round-trip cost. Entry is the next bar open. Exit is 20 bars later for daily history or 6 bars later in the same session for five-minute history. These forecasts do not validate the separately displayed ATR stop/target scenarios.

At least 180 labelled observations are required. A chronological 70/30 split purges training labels that extend into the test period. Scaling fits the training set only. The fitted training model predicts the latest completed bar; it is not refitted on test labels. Reports show test direction accuracy, constant-prior baseline, Brier score, nonoverlapping simulated trade count, win rate and confidence interval, mean net return, profit factor and drawdown. The 65% threshold is fixed and uncalibrated. It is not a claim of 65% achieved accuracy.

## Improve reliability before using predictions

1. Define a measurable target separately for intraday, options and investing: net profitable executed trades over a specific horizon, including fees and slippage. Also measure expectancy, drawdown, coverage and capital usage; high win rate alone can hide large losses.
2. Collect timestamped, corporate-action-checked histories across bull, bear and sideways markets. For news, store first-available publication times, revisions and entity mappings. For options, store actual expiry, strike, bid/ask, OI, volume, underlying and exchange timestamps. Do not attach today's news or surviving contracts to older prices. Start recording missing history rather than pretending it exists.
3. Train quantitative models on causal features. Compare against simple trend and constant baselines. Separate intraday and longer-horizon targets; use sector-specific fundamentals for investment research. Add historical news and OI only once the aligned dataset exists. Neither is currently a learned feature.
4. Extend the current single holdout to purged rolling walk-forward folds. Tune on validation folds and reserve a final untouched period. Account for overlapping labels, market regime changes, stock delistings and universe selection. Repeatedly choosing the best holdout destroys its independence.
5. Simulate realistic brokerage, exchange charges, STT, spread, latency, slippage, gaps, liquidity, stop/target fills, intraday square-off, lot sizes and option expiry. The current simple cost assumption and close-based exits do not provide this execution simulation.
6. Calibrate probabilities on a separate validation period. Check reliability curves, Brier score and confidence intervals. Allow the model to abstain when evidence is weak or data is stale; report the percentage of opportunities traded alongside accuracy.
7. Paper trade with frozen predictions, timestamps and costs for several months and market conditions. Compare predicted probabilities with actual outcomes, track drawdown and drift, and retrain only on a documented schedule. Paper-trading logging and automated retraining are future extensions.

There is no training recipe that guarantees more than 90% profitable trades or future profit. A model producing a 90% number is not evidence of 90% reliability. This implementation intentionally keeps forecasts experimental and transactions disabled.
