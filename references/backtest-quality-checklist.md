# Backtest Quality Checklist

Use this checklist whenever a strategy is being backtested, validated or compared.

## 1. Data Integrity

- Is the data source identified and trustworthy?
- Are missing candles / trades handled explicitly?
- Are timestamps aligned correctly across sources?
- Are corporate actions, contract rolls or symbol migrations relevant?
- Is survivorship bias a concern?

## 2. Causality

- Is there any look-ahead bias?
- Are signals computed only from data available at that time?
- Are execution prices realistic relative to signal timing?
- Are latency assumptions explicit?

## 3. Cost Modeling

- Are fees included?
- Is slippage modeled realistically?
- Are funding, borrow or carry costs included when relevant?
- Are maker/taker assumptions justified?
- Are gas costs modeled for on-chain strategies?

## 4. Execution Realism

- Are fills assumed or simulated plausibly?
- Does the backtest ignore queue position or liquidity constraints that matter live?
- Are partial fills relevant?
- Are stop losses or liquidation events modeled realistically?
- Are venue outages, stale quotes or market halts ignored in a dangerous way?

## 5. Metrics

- Are Sharpe, Sortino, drawdown, win rate and profit factor reported?
- Is the distribution of returns / trades shown?
- Is the average trade meaningful after costs?
- Is capital efficiency evaluated?

## 6. Robustness

- Is there an out-of-sample period?
- Are stress scenarios or regime changes considered?
- Is parameter sensitivity explored?
- Is the strategy only good in one narrow configuration?
- Is overfitting explicitly discussed?

## 7. Benchmarking

- Is there a baseline such as buy & hold, passive carry or naive execution?
- Does the strategy beat the baseline after costs and risk adjustment?
- Is complexity justified by the incremental edge?

## 8. Interpretation

- What would invalidate the backtest results?
- Which assumptions are doing the heavy lifting?
- Which live risks are still unmodeled?
- Is paper alpha likely to disappear in production?

## Review Rule

If the backtest depends on an assumption that would be hard to achieve live, call it out explicitly.
