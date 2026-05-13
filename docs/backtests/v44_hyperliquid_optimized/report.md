# v44 Hyperliquid BTC — Strict Validation

**Verdict:** NO-GO

## v44 Design Changes from v40

| # | Change | Purpose |
|---|--------|---------|
| 1 | ADX(14) > 20 filter | Avoid sideways chop that bleeds funding |
| 2 | Funding-rate adverse filter | Skip entry when 1h funding > 0.01% OR 8h avg > 0.005% against position |

**What v44 removes from v43:** trailing stop, max hold time, profit target.
v44 is a minimal filter layer on top of v40, without v43's complexity.

## Assumptions
- BTC only, 4h execution, daily EMA200 regime filter, EMA50/EMA200 trend logic
- ATR(14) × 3 initial stop, 2% risk per trade
- Reliable test window: longest continuous Hyperliquid 4h segment
- Next-bar-open execution for signal-driven entries/exits
- Stop losses: conservative stop-market handling (worse of stop vs bar open + adverse slippage)
- Hyperliquid taker fees (4.5 bps) on every entry/exit
- Exact Hyperliquid historical hourly funding applied
- Daily EMA200 regime uses previous completed daily bar (1-day shift, no look-ahead)
- Funding filter uses latest available 1h rate and trailing 8h average at bar close
- NO trailing stop, NO max hold time, NO profit target

## Scenario Table
| scenario | return_pct | cagr | max_dd | pf | wr | trades | sharpe | sortino | fees_total | funding_total |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| optimistic_plausible | -0.54 | -0.25 | -17.57 | 0.99 | 22.22 | 27 | 0.074 | 0.088 | 110.62 | -790.3 |
| baseline_realistic | 0.03 | 0.02 | -17.65 | 1.0 | 22.22 | 27 | 0.089 | 0.106 | 110.21 | -786.31 |
| stressed_conservative | -7.26 | -3.4 | -19.93 | 0.83 | 22.22 | 27 | -0.102 | -0.117 | 105.39 | -1402.89 |

## v40 vs v43 vs v44 Comparison (Baseline Scenario)

| Metric | v40 Baseline | v43 Baseline | v44 Baseline |
|--------|-------------|-------------|-------------|
| return_pct | 3.55 | -21.49 | 0.03 |
| cagr | 1.61 | -10.5 | 0.02 |
| max_dd | -18.7 | -23.67 | -17.65 |
| pf | 1.08 | 0.76 | 1.0 |
| wr | 22.22 | 40.35 | 22.22 |
| trades | 27 | 114 | 27 |
| sharpe | 0.179 | -0.714 | 0.089 |
| sortino | 0.215 | -0.697 | 0.106 |
| fees_total | 112.76 | 407.23 | 110.21 |
| funding_total | -854.85 | -443.1 | -786.31 |

## Stressed Scenario Comparison

| Metric | v40 Stressed | v43 Stressed | v44 Stressed |
|--------|-------------|-------------|-------------|
| return_pct | -4.33 | -27.62 | -7.26 |
| cagr | -2.01 | -13.78 | -3.4 |
| max_dd | -20.31 | -28.6 | -19.93 |
| pf | 0.9 | 0.69 | 0.83 |
| wr | 22.22 | 38.6 | 22.22 |
| trades | 27 | 114 | 27 |
| sharpe | -0.014 | -0.966 | -0.102 |
| sortino | -0.017 | -0.932 | -0.117 |
| fees_total | 107.49 | 390.62 | 105.39 |
| funding_total | -1509.84 | -781.31 | -1402.89 |

## Baseline Passive
- Passive long buy-and-hold on same window: return 20.14%, CAGR 8.38%, max drawdown -49.89%, funding pnl -6446.06.

## Methodology Notes
- **Design changes**: 2 filters over v40 (ADX + funding), NO trailing/PT/max-hold.
- **No parameter optimization**: v44 uses v40 base params plus fixed thresholds (ADX>20, funding 0.01%/0.005%).
- **Reason tracking**: Each trade tagged with exit reason (stop, ema_inverse_next_open, eod).

## Recommendation
The v44 edge does not survive conservative execution stress cleanly enough for live deployment. Use this only for testnet validation of the plumbing, and do not move to live until the strategy is reworked or materially improved.

## Diagnostics
```json
{
  "generated_at_utc": "2026-05-13T16:21:22.228031+00:00",
  "methodology": {
    "execution": "next 4h bar open for signal-driven entries/exits; intrabar conservative stop-market fill",
    "daily_regime_alignment": "previous completed daily EMA200 shifted by one day before 4h forward-fill",
    "funding": "exact Hyperliquid hourly fundingHistory for BTC; filter uses latest 1h rate + 8h trailing average at bar close",
    "adx": "ADX(14) with Wilder's smoothing; entry allowed only when > 20",
    "no_trailing_stop": true,
    "no_max_hold_time": true,
    "no_profit_target": true,
    "reliable_window_only": true
  },
  "v44_filters": {
    "adx_threshold": 20.0,
    "funding_1h_threshold": 0.0001,
    "funding_8h_threshold": 5e-05
  },
  "filter_coverage": {
    "adx_ok_pct": 72.16,
    "funding_ok_long_pct": 93.26,
    "funding_ok_short_pct": 100.0
  },
  "data_integrity": {
    "hyperliquid_4h_full": {
      "bars": 5720,
      "duplicates": 0,
      "missing_bars": 1843,
      "first": "2022-11-30T04:00:00+00:00",
      "last": "2026-05-13T12:00:00+00:00",
      "sample_missing": [
        "2023-03-30T04:00:00+00:00",
        "2023-03-30T08:00:00+00:00",
        "2023-03-30T12:00:00+00:00",
        "2023-03-30T16:00:00+00:00",
        "2023-03-30T20:00:00+00:00",
        "2023-03-31T00:00:00+00:00",
        "2023-03-31T04:00:00+00:00",
        "2023-03-31T08:00:00+00:00",
        "2023-03-31T12:00:00+00:00",
        "2023-03-31T16:00:00+00:00"
      ]
    },
    "reliable_continuous_4h_segment": {
      "start": "2024-01-31T08:00:00+00:00",
      "end": "2026-05-13T12:00:00+00:00",
      "bars": 5000
    },
    "strict_4h_window": {
      "bars": 5000,
      "duplicates": 0,
      "missing_bars": 0,
      "first": "2024-01-31T08:00:00+00:00",
      "last": "2026-05-13T12:00:00+00:00",
      "sample_missing": []
    },
    "strict_1d_window": {
      "bars": 1959,
      "duplicates": 0,
      "missing_bars": 0,
      "first": "2021-01-01T00:00:00+00:00",
      "last": "2026-05-13T00:00:00+00:00",
      "sample_missing": []
    },
    "funding_window": {
      "bars": 19995,
      "duplicates": 0,
      "missing_bars": 1,
      "first": "2024-01-31T08:00:00+00:00",
      "last": "2026-05-13T11:00:00+00:00",
      "sample_missing": [
        "2024-08-15T13:00:00+00:00"
      ]
    }
  },
  "buy_hold_overlap_same_window": {
    "return_pct": 20.14,
    "cagr": 8.38,
    "max_dd": -49.89,
    "funding_pnl": -6446.06
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
        "description": "Next-open execution, taker fees, light slippage, exact signed funding \u2014 v44 filters active",
        "initial_capital": 10000.0,
        "risk_pct": 0.02,
        "stop_atr_mult": 3.0,
        "warmup_bars": 220
      },
      "summary": {
        "return_pct": -0.54,
        "cagr": -0.25,
        "max_dd": -17.57,
        "pf": 0.99,
        "wr": 22.22,
        "trades": 27,
        "sharpe": 0.074,
        "sortino": 0.088
      },
      "cost_breakdown": {
        "fees_total": 110.62,
        "funding_total": -790.3
      },
      "trade_distribution": {
        "avg_trade_pnl": -2.01,
        "median_trade_pnl": -189.32,
        "avg_hold_hours": 515.11,
        "median_hold_hours": 236.0
      },
      "liquidity": {
        "max_qty_pct_of_bar_volume": 0.014246,
        "p95_qty_pct_of_bar_volume": 0.010184
      },
      "exit_reasons": {
        "stop": 18,
        "ema_inverse_next_open": 9
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
        "description": "Next-open execution, taker fees, moderate slippage, exact signed funding \u2014 v44 filters active",
        "initial_capital": 10000.0,
        "risk_pct": 0.02,
        "stop_atr_mult": 3.0,
        "warmup_bars": 220
      },
      "summary": {
        "return_pct": 0.03,
        "cagr": 0.02,
        "max_dd": -17.65,
        "pf": 1.0,
        "wr": 22.22,
        "trades": 27,
        "sharpe": 0.089,
        "sortino": 0.106
      },
      "cost_breakdown": {
        "fees_total": 110.21,
        "funding_total": -786.31
      },
      "trade_distribution": {
        "avg_trade_pnl": 0.13,
        "median_trade_pnl": -189.61,
        "avg_hold_hours": 520.0,
        "median_hold_hours": 252.0
      },
      "liquidity": {
        "max_qty_pct_of_bar_volume": 0.014231,
        "p95_qty_pct_of_bar_volume": 0.010179
      },
      "exit_reasons": {
        "stop": 17,
        "ema_inverse_next_open": 10
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
        "description": "Next-open execution, taker fees, heavy slippage, stop stress, only adverse funding counted and magnified \u2014 v44 filters active",
        "initial_capital": 10000.0,
        "risk_pct": 0.02,
        "stop_atr_mult": 3.0,
        "warmup_bars": 220
      },
      "summary": {
        "return_pct": -7.26,
        "cagr": -3.4,
        "max_dd": -19.93,
        "pf": 0.83,
        "wr": 22.22,
        "trades": 27,
        "sharpe": -0.102,
        "sortino": -0.117
      },
      "cost_breakdown": {
        "fees_total": 105.39,
        "funding_total": -1402.89
      },
      "trade_distribution": {
        "avg_trade_pnl": -26.9,
        "median_trade_pnl": -192.39,
        "avg_hold_hours": 519.7,
        "median_hold_hours": 252.0
      },
      "liquidity": {
        "max_qty_pct_of_bar_volume": 0.014022,
        "p95_qty_pct_of_bar_volume": 0.010093
      },
      "exit_reasons": {
        "stop": 17,
        "ema_inverse_next_open": 10
      }
    }
  }
}
```