# BTC Trend-Following 4h

> Formerly known as "v40"

## What It Does

Long-short trend-following strategy for BTC perpetual futures on the **4h timeframe**, using EMA50/EMA200 crossovers as the primary signal and a **daily EMA200 regime filter** to avoid counter-trend trades. Only goes long when the daily regime is bullish (price > daily EMA200), only goes short when bearish (price < daily EMA200).

## Target Market & Venue

- **Asset**: BTC/USDC (Hyperliquid) or BTC/USDT (Binance Futures)
- **Venue**: Hyperliquid Perps (default) or Binance Futures
- **Timeframe**: 4h candles for signals, daily candles for regime filter

## Strategy Logic

### Entry Rules

| Condition | Long | Short |
|-----------|------|-------|
| 4h EMA alignment | close > EMA50 > EMA200 | close < EMA50 < EMA200 |
| Daily regime | close > EMA200_daily | close < EMA200_daily |

Both conditions must be true simultaneously.

### Exit Rules

1. **Stop loss**: ATR-based trailing stop at `entry +/- (ATR * 3.0)`
2. **Trend reversal**: EMA50 crosses below EMA200 (for longs) or above (for shorts)

### Position Sizing

Risk-per-trade model: `size = (equity * 0.02) / |entry - stop|`
- Risks 2% of equity per trade
- Position size adjusts automatically based on stop distance

## Backtest Results

> TODO: Run backtest and fill in actual results.

| Metric | Value |
|--------|-------|
| Period | — |
| CAGR | — |
| Max Drawdown | — |
| Profit Factor | — |
| Total Trades | — |
| Win Rate | — |

## Recommended Parameters

| Parameter | Default | Notes |
|-----------|---------|-------|
| EMA Fast | 50 | 4h trend signal |
| EMA Slow | 200 | 4h trend signal |
| Daily EMA | 200 | Regime filter |
| ATR Period | 14 | Volatility measurement |
| Stop ATR Mult | 3.0 | Stop loss distance |
| Risk % | 0.02 | 2% per trade |

## How to Configure

1. Copy `.env.example` to `.env` and fill in your API keys
2. Edit `config.yaml` to adjust strategy parameters (see Parameter Reference below)
3. Set `enabled: false` in `config.yaml` to disable this strategy

## How to Run

### Testnet (default)

```bash
# Run both strategies together
python -m bots.shared

# Run this strategy only
python -m bots.btc_trend_4h
```

### Mainnet

```bash
# In .env:
BOT_TESTNET=false

# Then run as above
python -m bots.shared
```

## How to Backtest

Use the backtest scripts in `scripts/`:

```bash
python scripts/binance_futures_v40_backtest.py
```

Or write a new backtest that imports the strategy:

```python
from bots.btc_trend_4h.strategy import BTCTrend4hStrategy

strategy = BTCTrend4hStrategy(ema_fast=50, ema_slow=200)
df_ind = strategy.compute_indicators(df_4h, df_daily)
```

## Risk Warnings

- **Trend-following strategies lose money in ranging/choppy markets**. Expect drawdowns during consolidation periods.
- The daily regime filter reduces but does NOT eliminate whipsaw risk.
- ATR-based stops can gap through in fast markets — actual fill may be worse than stop price.
- This bot trades both long AND short. Shorting has theoretically unlimited risk.
- **Never risk more than you can afford to lose.** Start on testnet.

## Parameter Reference

| Parameter | What It Does | Valid Range | Default | Risk Implication |
|-----------|-------------|-------------|---------|-----------------|
| `ema_fast` | Fast EMA period for 4h trend | 10-100 | 50 | Lower = more signals, more false positives |
| `ema_slow` | Slow EMA period for 4h trend | 50-500 | 200 | Lower = tighter trend, earlier exits |
| `ema_regime_daily` | Daily EMA for regime filter | 50-500 | 200 | Higher = stricter filter, fewer trades |
| `atr_period` | ATR calculation period | 5-30 | 14 | Lower = more responsive stops |
| `stop_atr_mult` | Stop distance multiplier | 1.0-10.0 | 3.0 | Lower = tighter stops, more whipsaws |
| `risk_pct` | Risk per trade | 0.005-0.10 | 0.02 | Higher = larger positions, bigger drawdowns |
| `warmup_bars` | Bars before trading starts | 100-500 | 220 | Prevents unreliable indicator values |
| `enabled` | Enable/disable strategy | true/false | true | Set false to run solo |
