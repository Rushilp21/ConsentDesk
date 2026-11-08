# Short guide to the trading screens

## F&O recommendations

- **Buy / bullish → Buy Call (CE):** watch for a rise in the underlying.
- **Sell / bearish → Buy Put (PE):** watch for a fall in the underlying. This buys
  a put; it does not sell/write an option.
- Each section shows up to three ideas with a liquid listed contract, filtered
  by the selected sector. No qualifying idea means wait, rather than forced picks.
- Trigger, stop and target are prices of the underlying stock/index, **not option
  premium prices**. Premium stop/target and expiry exposure need broker review.
- One lot premium = ask premium per unit × verified lot size, before costs.
  It is not the margin required to write options or trade futures.
- This release provides option watchlists. Futures contract selection and
  option-writing strategies are not implemented.

Ideas use trend, momentum, volume, chart patterns and news evidence. OI describes
positioning and is not a standalone direction signal. Quote freshness, session,
lot-size and strategy gaps remain visible. A watchlist is not an executable order.

## Screenshot 1: open interest (OI)

OI counts outstanding positions. Call OI 34,30,600 and put OI 21,50,075 produce a
put/call ratio of about 0.627. There are fewer put positions than call positions;
that alone does not say which side will profit. Every contract has both a buyer
and a seller. Confirm the provider's units; OI is not an investment amount.

Call concentration at strike 2100 may act like resistance and put concentration
at 2000 may act like support. Both can break. Expiry 2026-11-23 is the contract's
end date. Negative OI changes mean fewer outstanding positions; they do not prove
buying or selling direction. “Fetched” is the download time, not verification of
a fresh exchange contract quote.

## Screenshot 2: evidence checklist

“20-bar breakout” means price crossed a recent range measured over 20 candles.
“Mixed swing structure” means recent highs/lows do not agree on direction.
4/7 means four bullish checks passed, not a 57% chance of profit.

RSI 70.6 is strong/stretched momentum; volume 5.12× means activity was much higher
than the comparison average. Above VWAP means price was above the session's
volume-weighted average. A mixed daily trend and only one news publisher weaken
confirmation. It is reasonable for these conflicts to keep the result at WAIT.

## Screenshot 3: historical model test

70.9% chart-direction accuracy was **below the simple baseline's 71.8%**.
Only two simulated trades occurred: one won, so the net win rate was 50%.
Mean return after assumed costs was -0.2% per trade. Drawdown 0.7% was the largest
simulated fall from a peak in this test. 20 bps = 0.20% assumed round-trip cost.

The 9.4%–90.5% win-rate interval is very wide because two trades are too few to
judge reliability. The three windows test different historical periods, not
three future forecasts. This tests a price classifier, not option premium fills,
news/OI profitability or real stop-loss execution. It is now under Advanced.

## Holding period

Long-term ideas use a **12–36 month planning window**, reviewed every 30 days and
after quarterly results. This is a stated policy across sectors, not a model
prediction of the best sell date. Missing fundamentals still mean research only.
Review earlier if the thesis fails, valuation becomes excessive or risk levels
break. Intraday and F&O watchlists are same-session only, with a 15:15 IST exit
time limit and earlier exit at stop/target. Overnight derivative holding is not
part of these plans.

References: [NSE contract specifications](https://www.nseindia.com/static/products-services/equity-derivatives-individual-securities),
[SEBI derivatives introduction](https://investor.sebi.gov.in/hindi/understanding_derivatives.html),
[OIC technical information](https://www.optionseducation.org/referencelibrary/faq/technical-information).
