# Trading Review Checklist

Use this checklist when reviewing a strategy, execution engine, bot architecture or risky change.

## 1. Strategy / Edge

- Is the edge explicitly stated?
- Is the edge testable or falsifiable?
- Does profitability depend on unrealistic assumptions?
- Is the strategy regime-dependent?
- Are failure / invalidation conditions defined?

## 2. Market Structure

- Which venue(s) and market type are involved?
- Are liquidity and spread assumptions realistic?
- Are queue position and fill quality relevant?
- Could MEV, adverse selection or toxic flow destroy the edge?
- Are fees, rebates, funding or borrow costs included?

## 3. Execution

- Are order states and transitions defined clearly?
- Is duplicate-order risk controlled?
- Are cancel/replace races handled?
- Are partial fills handled correctly?
- Is idempotency guaranteed where needed?
- Are retries bounded and safe?
- Is stale market data detected?
- Are venue outages or API degradation considered?

## 4. Risk

- Are position limits defined?
- Are leverage and liquidation risks understood?
- Are kill switches or circuit breakers defined?
- Is inventory risk controlled?
- Is drawdown control specified?
- Are correlations / concentration risks considered?

## 5. Data / Modeling

- Is the data source reliable?
- Is there leakage or look-ahead bias risk?
- Is the timestamp alignment correct?
- Are corporate actions / roll adjustments / symbol mapping issues relevant?
- Are event-resolution assumptions explicit for prediction markets?

## 6. Observability / Ops

- Are logs sufficient to reconstruct failures?
- Are alerts defined for risk or execution anomalies?
- Is there health monitoring?
- Can operators stop or disable the bot safely?
- Is there a dead-man switch or equivalent safety control if required?

## 7. Architecture

- Are strategy logic, risk rules and execution concerns separated?
- Are infrastructure details leaking into domain logic?
- Is the design testable?
- Are config and secrets handled safely?
- Is the change consistent with project conventions?

## 8. Backtesting / Validation

- Are assumptions documented?
- Are fees, slippage and latency modeled reasonably?
- Is overfitting discussed?
- Is there a baseline comparison?
- Are out-of-sample or stress scenarios considered?

## Review Rule

If a critical checklist item is missing, call it out explicitly instead of assuming it exists.
