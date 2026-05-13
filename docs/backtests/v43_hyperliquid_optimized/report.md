# v43 Hyperliquid BTC — Strict Validation

**Verdict:** NO-GO

## v43 Design Changes from v40

| # | Change | Purpose |
|---|--------|---------|
| 1 | ADX(14) > 20 filter | Avoid sideways chop that bleeds funding |
| 2 | Funding-rate adverse filter | Skip entry when 1h funding > 0.01% OR 8h avg > 0.005% against position |
| 3 | Trailing stop | Activate after 1×ATR profit, trail at 3×ATR, never tighten |
| 4 | Profit target 3:1 R:R | Close 50% at entry ± 9×ATR, rest trails |
| 5 | Max holding time | Force close after 30 bars (120h) to cap funding exposure |

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

## Scenario Table
| scenario | return_pct | cagr | max_dd | pf | wr | trades | sharpe | sortino | fees_total | funding_total |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| optimistic_plausible | -19.25 | -9.34 | -22.0 | 0.78 | 41.23 | 114 | -0.626 | -0.613 | 412.83 | -446.58 |
| baseline_realistic | -21.49 | -10.5 | -23.67 | 0.76 | 40.35 | 114 | -0.714 | -0.697 | 407.23 | -443.1 |
| stressed_conservative | -27.62 | -13.78 | -28.6 | 0.69 | 38.6 | 114 | -0.966 | -0.932 | 390.62 | -781.31 |

## v40 vs v43 Comparison (Baseline Scenario)

| Metric | v40 Baseline | v43 Baseline | Δ |
|--------|-------------|-------------|---|
| return_pct | 3.55 | -21.49 | -25.04 |
| cagr | 1.61 | -10.5 | -12.11 |
| max_dd | -18.7 | -23.67 | -4.97 |
| pf | 1.08 | 0.76 | -0.32 |
| wr | 22.22 | 40.35 | +18.13 |
| trades | 27 | 114 | +87.0 |
| sharpe | 0.179 | -0.714 | -0.89 |
| sortino | 0.215 | -0.697 | -0.91 |
| fees_total | 112.76 | 407.23 | +294.47 |
| funding_total | -854.85 | -443.1 | +411.75 |

## Baseline Passive
- Passive long buy-and-hold on same window: return 22.91%, CAGR 9.47%, max drawdown -49.89%, funding pnl -6385.8.

## Methodology Notes
- **Design changes**: 5 enhancements tested simultaneously — the result is the combined effect.
- **No parameter optimization**: v43 uses v40 base params plus fixed thresholds (ADX>20, funding 0.01%/0.005%, 3:1 R:R, 30-bar cap).
- **Reason tracking**: Each trade tagged with exit reason (stop, trailing_stop, ema_inverse, max_hold_time, profit_target_50pct, eod).

## Recommendation
The v43 edge does not survive conservative execution stress cleanly enough for live deployment. Use this only for testnet validation of the plumbing, and do not move to live until the strategy is reworked or materially improved.

## Diagnostics
```json
{
  "generated_at_utc": "2026-05-13T15:34:41.369676+00:00",
  "methodology": {
    "execution": "next 4h bar open for signal-driven entries/exits; intrabar conservative stop-market fill; intrabar profit target at 3:1 R:R",
    "daily_regime_alignment": "previous completed daily EMA200 shifted by one day before 4h forward-fill",
    "funding": "exact Hyperliquid hourly fundingHistory for BTC; filter uses latest 1h rate + 8h trailing average at bar close",
    "adx": "ADX(14) with Wilder's smoothing; entry allowed only when > 20",
    "trailing_stop": "activates at 1x ATR profit; trails at 3x ATR; never resets tighter",
    "profit_target": "intrabar partial close 50% at 3:1 R:R (entry \u00b1 9\u00d7ATR_at_entry)",
    "max_hold_time": "force close after 30 bars (120 hours) to cap funding drag",
    "reliable_window_only": true
  },
  "v43_filters": {
    "adx_threshold": 20.0,
    "funding_1h_threshold": 0.0001,
    "funding_8h_threshold": 5e-05,
    "trail_activation_atr_mult": 1.0,
    "profit_target_rr": 3.0,
    "max_hold_bars": 30
  },
  "filter_coverage": {
    "adx_ok_pct": 72.14,
    "funding_ok_long_pct": 93.26,
    "funding_ok_short_pct": 100.0
  },
  "data_integrity": {
    "hyperliquid_4h_full": {
      "bars": 5720,
      "duplicates": 0,
      "missing_bars": 1842,
      "first": "2022-11-30T04:00:00+00:00",
      "last": "2026-05-13T08:00:00+00:00",
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
      "start": "2024-01-31T04:00:00+00:00",
      "end": "2026-05-13T08:00:00+00:00",
      "bars": 5000
    },
    "strict_4h_window": {
      "bars": 5000,
      "duplicates": 0,
      "missing_bars": 0,
      "first": "2024-01-31T04:00:00+00:00",
      "last": "2026-05-13T08:00:00+00:00",
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
      "first": "2024-01-31T04:00:00+00:00",
      "last": "2026-05-13T07:00:00+00:00",
      "sample_missing": [
        "2024-08-15T13:00:00+00:00"
      ]
    }
  },
  "buy_hold_overlap_same_window": {
    "return_pct": 22.91,
    "cagr": 9.47,
    "max_dd": -49.89,
    "funding_pnl": -6385.8
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
        "description": "Next-open execution, taker fees, light slippage, exact signed funding \u2014 v43 filters active",
        "initial_capital": 10000.0,
        "risk_pct": 0.02,
        "stop_atr_mult": 3.0,
        "warmup_bars": 220,
        "profit_target_rr": 3.0,
        "trail_activation_atr_mult": 1.0,
        "max_hold_bars": 30,
        "adx_threshold": 20.0
      },
      "summary": {
        "return_pct": -19.25,
        "cagr": -9.34,
        "max_dd": -22.0,
        "pf": 0.78,
        "wr": 41.23,
        "trades": 114,
        "sharpe": -0.626,
        "sortino": -0.613
      },
      "cost_breakdown": {
        "fees_total": 412.83,
        "funding_total": -446.58
      },
      "trade_distribution": {
        "avg_trade_pnl": -16.89,
        "median_trade_pnl": -37.22,
        "avg_hold_hours": 78.07,
        "median_hold_hours": 84.0,
        "avg_bars_held": 19.24,
        "median_bars_held": 21.0
      },
      "liquidity": {
        "max_qty_pct_of_bar_volume": 0.094451,
        "p95_qty_pct_of_bar_volume": 0.017206
      },
      "exit_reasons": {
        "trailing_stop": 41,
        "stop": 36,
        "max_hold_time": 31,
        "profit_target_50pct": 5,
        "ema_inverse_next_open": 1
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
        "description": "Next-open execution, taker fees, moderate slippage, exact signed funding \u2014 v43 filters active",
        "initial_capital": 10000.0,
        "risk_pct": 0.02,
        "stop_atr_mult": 3.0,
        "warmup_bars": 220,
        "profit_target_rr": 3.0,
        "trail_activation_atr_mult": 1.0,
        "max_hold_bars": 30,
        "adx_threshold": 20.0
      },
      "summary": {
        "return_pct": -21.49,
        "cagr": -10.5,
        "max_dd": -23.67,
        "pf": 0.76,
        "wr": 40.35,
        "trades": 114,
        "sharpe": -0.714,
        "sortino": -0.697
      },
      "cost_breakdown": {
        "fees_total": 407.23,
        "funding_total": -443.1
      },
      "trade_distribution": {
        "avg_trade_pnl": -18.85,
        "median_trade_pnl": -39.55,
        "avg_hold_hours": 78.07,
        "median_hold_hours": 84.0,
        "avg_bars_held": 19.24,
        "median_bars_held": 21.0
      },
      "liquidity": {
        "max_qty_pct_of_bar_volume": 0.094259,
        "p95_qty_pct_of_bar_volume": 0.01709
      },
      "exit_reasons": {
        "trailing_stop": 41,
        "stop": 36,
        "max_hold_time": 31,
        "profit_target_50pct": 5,
        "ema_inverse_next_open": 1
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
        "description": "Next-open execution, taker fees, heavy slippage, stop stress, only adverse funding counted and magnified \u2014 v43 filters active",
        "initial_capital": 10000.0,
        "risk_pct": 0.02,
        "stop_atr_mult": 3.0,
        "warmup_bars": 220,
        "profit_target_rr": 3.0,
        "trail_activation_atr_mult": 1.0,
        "max_hold_bars": 30,
        "adx_threshold": 20.0
      },
      "summary": {
        "return_pct": -27.62,
        "cagr": -13.78,
        "max_dd": -28.6,
        "pf": 0.69,
        "wr": 38.6,
        "trades": 114,
        "sharpe": -0.966,
        "sortino": -0.932
      },
      "cost_breakdown": {
        "fees_total": 390.62,
        "funding_total": -781.31
      },
      "trade_distribution": {
        "avg_trade_pnl": -24.22,
        "median_trade_pnl": -46.01,
        "avg_hold_hours": 78.0,
        "median_hold_hours": 84.0,
        "avg_bars_held": 19.22,
        "median_bars_held": 21.0
      },
      "liquidity": {
        "max_qty_pct_of_bar_volume": 0.093418,
        "p95_qty_pct_of_bar_volume": 0.016721
      },
      "exit_reasons": {
        "trailing_stop": 41,
        "stop": 36,
        "max_hold_time": 31,
        "profit_target_50pct": 5,
        "ema_inverse_next_open": 1
      }
    }
  }
}
```