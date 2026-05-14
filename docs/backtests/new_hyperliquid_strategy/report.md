# v47 RSI Momentum Pullback — Strict Validation

**Verdict:** GO

## Strategy Design

### Core Hypothesis
In strong trends (ADX > 25, +DI > -DI for longs), RSI pullbacks to 35-50 represent
temporary value zones. The KEY improvement over v46: RSI must be RISING (vs 2 bars ago)
for longs, or FALLING for shorts — confirming the pullback is ending and momentum
is resuming in trend direction. This filters out entries where RSI is still dropping.

### Entry (all 4 conditions on 2h bar close)
| # | Condition | Parameter |
|---|-----------|-----------|
| 1 | Strong trend | +DI > -DI AND ADX(14) > 25 (long) / opposite (short) |
| 2 | RSI pullback | RSI(14) 35–50 (long) / 50–65 (short) |
| 3 | Candle confirms | Bullish close (long) / Bearish close (short) |
| 4 | RSI momentum | RSI rising vs 2 bars ago (long) / falling (short) |

### Exit
| Trigger | Detail |
|---------|--------|
| Trailing stop | 3× ATR(14) from peak/high-water mark |
| Max hold cap | 12 bars (24h) forced exit |
| Initial stop | 3× ATR(14) from entry (before trail takes over) |

### Key Differences from v46
- **RSI momentum confirmation**: RSI rising/falling vs 2 bars ago
- **RSI ranges adjusted**: 35-50/50-65 (realistic for trend context)
- **Removed daily EMA200**: redundant with ADX/DI trend filter
- **Same exit structure**: 3x ATR trail + max 12 bars

### Why not extreme RSI ranges (20-40/60-80)?
Data analysis shows: in uptrends (ADX>25), mean RSI = 63. RSI never drops below 30.
In downtrends, mean RSI = 39.5. RSI never rises above 70. Extreme RSI levels only
occur in ranging/choppy markets — the opposite of what we want for trend-following.

## Assumptions
- BTC only, 2h execution
- ATR(14) × 3.0 stop/trail, 1.5% risk per trade
- Max hold: 12 bars (24h on 2h candles)
- Next-bar-open execution for entries/exits
- Conservative stop-market handling
- HL taker fees 4.5 bps, exact hourly funding
- EMA50 (2h) trend filter, no daily regime

## Scenario Table
| scenario | return_pct | cagr | max_dd | pf | wr | trades | sharpe | sortino | fees_total | funding_total |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| optimistic_plausible | 4.39 | 4.0 | -1.76 | 2.59 | 69.23 | 13 | 1.352 | 0.498 | 65.93 | 12.5 |
| baseline_realistic | 4.03 | 3.68 | -1.87 | 2.4 | 69.23 | 13 | 1.246 | 0.466 | 65.83 | 12.5 |
| stressed_conservative | 3.33 | 3.04 | -2.06 | 2.07 | 61.54 | 13 | 1.032 | 0.393 | 65.62 | -5.5 |

## v47 vs v46 vs v40 Comparison (Baseline)

| Metric | v40 Baseline | v46 Baseline | v47 Baseline | Δ v47-v46 |
|--------|-------------|-------------|-------------|-----------|
| return_pct | 3.55 | -3.72 | 4.03 | +7.75 |
| cagr | 1.61 | -3.41 | 3.68 | +7.09 |
| max_dd | -18.7 | -6.11 | -1.87 | +4.24 |
| pf | 1.08 | 0.66 | 2.4 | +1.74 |
| wr | 22.22 | 45.0 | 69.23 | +24.23 |
| trades | 27 | 20 | 13 |  |
| sharpe | 0.179 | -0.874 | 1.246 | +2.12 |
| sortino | 0.215 | -0.248 | 0.466 | +0.71 |
| fees_total | 112.76 | 98.48 | 65.83 | -32.65 |
| funding_total | -854.85 | 9.63 | 12.5 | +2.87 |

## Stressed Scenario Comparison

| Metric | v40 Stressed | v46 Stressed | v47 Stressed |
|--------|-------------|-------------|-------------|
| return_pct | -4.33 | -4.99 | 3.33 |
| cagr | -2.01 | -4.56 | 3.04 |
| max_dd | -20.31 | -6.7 | -2.06 |
| pf | 0.9 | 0.57 | 2.07 |
| wr | 22.22 | 40.0 | 61.54 |
| trades | 27 | 20 | 13 |
| sharpe | -0.014 | -1.136 | 1.032 |
| sortino | -0.017 | -0.309 | 0.393 |
| fees_total | 107.49 | 97.77 | 65.62 |
| funding_total | -1509.84 | -10.0 | -5.5 |

## Baseline Passive
- Buy-and-hold: return -8.65%, CAGR -7.94%, max DD -49.9%, funding -1237.09.

## Recommendation
Survives strict realism. Justify tiny live pilot.

## Diagnostics
```json
{
  "generated_at_utc": "2026-05-14T12:53:12.080814+00:00",
  "strategy": {
    "name": "v47 RSI Momentum Pullback",
    "timeframe": "2h",
    "entry": [
      "+DI > -DI + ADX>25",
      "RSI 35-50 long / 50-65 short",
      "Bullish/bearish candle",
      "RSI rising/falling vs 2 bars ago"
    ],
    "exit": [
      "Trail stop 3x ATR from peak",
      "Max hold 12 bars (24h)",
      "Initial stop 3x ATR from entry"
    ],
    "risk": "1.5% per trade",
    "max_hold_hours": 24
  },
  "methodology": {
    "execution": "next 2h bar open",
    "funding": "exact HL hourly fundingHistory"
  },
  "signal_diagnostics": {
    "test_bars": 4800,
    "long": 3,
    "short": 16,
    "total": 19,
    "est_per_week": 0.3
  },
  "data_integrity": {
    "strict_2h": {
      "bars": 5000,
      "duplicates": 0,
      "missing_bars": 0,
      "first": "2025-03-23T20:00:00+00:00",
      "last": "2026-05-14T10:00:00+00:00",
      "sample_missing": []
    },
    "strict_1d": {
      "bars": 1960,
      "duplicates": 0,
      "missing_bars": 0,
      "first": "2021-01-01T00:00:00+00:00",
      "last": "2026-05-14T00:00:00+00:00",
      "sample_missing": []
    }
  },
  "buy_hold": {
    "return_pct": -8.65,
    "cagr": -7.94,
    "max_dd": -49.9,
    "funding_pnl": -1237.09
  },
  "scenario_details": {
    "optimistic_plausible": {
      "config": {
        "name": "optimistic_plausible",
        "fee_rate": 0.00045,
        "entry_slippage_bps": 1.0,
        "exit_slippage_bps": 1.0,
        "stop_slippage_bps": 3.0,
        "funding_mode": "actual_signed",
        "funding_multiplier": 1.0,
        "description": "Light slippage, exact funding",
        "initial_capital": 10000.0,
        "risk_pct": 0.015,
        "stop_atr_mult": 3.0,
        "trail_atr_mult": 3.0,
        "tp_atr_mult": 0.0,
        "max_hold_bars": 12,
        "warmup_bars": 200
      },
      "summary": {
        "return_pct": 4.39,
        "cagr": 4.0,
        "max_dd": -1.76,
        "pf": 2.59,
        "wr": 69.23,
        "trades": 13,
        "sharpe": 1.352,
        "sortino": 0.498
      },
      "cost_breakdown": {
        "fees_total": 65.93,
        "funding_total": 12.5
      },
      "trade_distribution": {
        "avg_trade_pnl": 33.74,
        "median_trade_pnl": 18.63,
        "avg_hold_hours": 22.31,
        "median_hold_hours": 24.0,
        "p95_hold_hours": 24.0
      },
      "liquidity": {
        "max_qty_pct_of_bar_volume": 0.011232,
        "p95_qty_pct_of_bar_volume": 0.008683
      },
      "exit_reasons": {
        "max_hold": 10,
        "trail_stop": 3
      },
      "funding_analysis": {
        "total_funding": 12.5,
        "funding_per_trade": 0.96,
        "funding_per_hour_held": 0.0431,
        "total_hold_hours": 290.0
      },
      "holding_time": {
        "avg": 22.31,
        "median": 24.0,
        "p95": 24.0
      }
    },
    "baseline_realistic": {
      "config": {
        "name": "baseline_realistic",
        "fee_rate": 0.00045,
        "entry_slippage_bps": 3.0,
        "exit_slippage_bps": 3.0,
        "stop_slippage_bps": 8.0,
        "funding_mode": "actual_signed",
        "funding_multiplier": 1.0,
        "description": "Moderate slippage, exact funding",
        "initial_capital": 10000.0,
        "risk_pct": 0.015,
        "stop_atr_mult": 3.0,
        "trail_atr_mult": 3.0,
        "tp_atr_mult": 0.0,
        "max_hold_bars": 12,
        "warmup_bars": 200
      },
      "summary": {
        "return_pct": 4.03,
        "cagr": 3.68,
        "max_dd": -1.87,
        "pf": 2.4,
        "wr": 69.23,
        "trades": 13,
        "sharpe": 1.246,
        "sortino": 0.466
      },
      "cost_breakdown": {
        "fees_total": 65.83,
        "funding_total": 12.5
      },
      "trade_distribution": {
        "avg_trade_pnl": 31.04,
        "median_trade_pnl": 17.85,
        "avg_hold_hours": 22.31,
        "median_hold_hours": 24.0,
        "p95_hold_hours": 24.0
      },
      "liquidity": {
        "max_qty_pct_of_bar_volume": 0.011232,
        "p95_qty_pct_of_bar_volume": 0.008671
      },
      "exit_reasons": {
        "max_hold": 10,
        "trail_stop": 3
      },
      "funding_analysis": {
        "total_funding": 12.5,
        "funding_per_trade": 0.96,
        "funding_per_hour_held": 0.0431,
        "total_hold_hours": 290.0
      },
      "holding_time": {
        "avg": 22.31,
        "median": 24.0,
        "p95": 24.0
      }
    },
    "stressed_conservative": {
      "config": {
        "name": "stressed_conservative",
        "fee_rate": 0.00045,
        "entry_slippage_bps": 6.0,
        "exit_slippage_bps": 6.0,
        "stop_slippage_bps": 15.0,
        "funding_mode": "adverse_only",
        "funding_multiplier": 1.5,
        "description": "Heavy slippage, adverse funding \u00d71.5",
        "initial_capital": 10000.0,
        "risk_pct": 0.015,
        "stop_atr_mult": 3.0,
        "trail_atr_mult": 3.0,
        "tp_atr_mult": 0.0,
        "max_hold_bars": 12,
        "warmup_bars": 200
      },
      "summary": {
        "return_pct": 3.33,
        "cagr": 3.04,
        "max_dd": -2.06,
        "pf": 2.07,
        "wr": 61.54,
        "trades": 13,
        "sharpe": 1.032,
        "sortino": 0.393
      },
      "cost_breakdown": {
        "fees_total": 65.62,
        "funding_total": -5.5
      },
      "trade_distribution": {
        "avg_trade_pnl": 25.64,
        "median_trade_pnl": 13.71,
        "avg_hold_hours": 22.31,
        "median_hold_hours": 24.0,
        "p95_hold_hours": 24.0
      },
      "liquidity": {
        "max_qty_pct_of_bar_volume": 0.011232,
        "p95_qty_pct_of_bar_volume": 0.008648
      },
      "exit_reasons": {
        "max_hold": 10,
        "trail_stop": 3
      },
      "funding_analysis": {
        "total_funding": -5.5,
        "funding_per_trade": -0.42,
        "funding_per_hour_held": -0.0189,
        "total_hold_hours": 290.0
      },
      "holding_time": {
        "avg": 22.31,
        "median": 24.0,
        "p95": 24.0
      }
    }
  },
  "v40_available": true,
  "v46_available": true
}
```