# v40 vs MACD+EMA Strategy Comparison — Hyperliquid BTC Perp

**Generated:** 2026-05-13T17:17:46.252972+00:00
**Reliable window:** 2024-01-31 → 2026-05-13 (5000 4h bars)

## Assumptions (IDENTICAL for ALL variants)
- Next-bar-open execution for all signal-driven entries/exits
- Hyperliquid taker fees: 4.5 bps per side
- Slippage: 3 bps entry/exit, 8 bps stop (baseline realistic)
- Exact hourly funding history from Hyperliquid API
- Conservative stop execution: worse of stop vs bar open + stop slippage
- 2% risk per trade, ATR(14)*3 stop for ALL variants
- Daily EMA200 regime uses previous completed daily bar (no look-ahead)
- Strategy logic unchanged from spec for each variant

## Comparison Table
| variant | timeframe | return_pct | cagr | max_dd | pf | wr | trades | sharpe | sortino | avg_hold_hours | fees_total | funding_total | avg_win | avg_loss |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| v40_4h | 4h | 13.48 | 5.97 | -16.99 | 1.32 | 22.22 | 27 | 0.41 | 0.507 | 528.1 | 120.41 | 76.74 | 917.26 | -197.9 |
| macd_ema50_4h | 4h | -14.81 | -7.09 | -21.84 | 0.85 | 30.15 | 199 | -0.414 | -0.404 | 38.4 | 807.77 | 15.1 | 144.8 | -73.16 |
| macd_daily_4h | 4h | -10.17 | -4.8 | -22.66 | 0.92 | 30.53 | 190 | -0.255 | -0.259 | 45.4 | 774.41 | 48.39 | 175.14 | -83.71 |
| macd_ema50_2h | 2h | -22.66 | -39.01 | -23.79 | 0.63 | 28.42 | 95 | -1.364 | -1.175 | 19.2 | 559.75 | 16.53 | 144.1 | -91.13 |
| macd_ema50_1h | 1h | -33.71 | -52.99 | -37.94 | 0.7 | 27.04 | 196 | -1.064 | -0.943 | 9.4 | 1650.39 | 22.87 | 149.48 | -78.79 |

## Baseline (Passive Long BTC Buy & Hold)
- Return: +77.31%, CAGR: +28.54%, MaxDD: -49.9%, Funding: $-561

## Verdict
✗ macd_ema50_4h underperforms v40 on 4h: -14.81% vs +13.48%

✗ macd_daily_4h underperforms v40 on 4h: -10.17% vs +13.48%



Best risk-adjusted return: v40_4h (Sortino=0.507, PF=1.32)



Best funding resistance: macd_ema50_4h ($+0.1/trade)



─── RECOMMENDATION ───

Top variant for Hyperliquid testnet validation: v40_4h

  Return: +13.48% | CAGR: +5.97% | Sortino: 0.507 | PF: 1.32

  Funding cost: $+77 ($+2.8/trade)