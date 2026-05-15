# BTC Momentum 1h

> Formerly known as "v48b"

## What It Does

Momentum pullback strategy for BTC perpetual futures on the **1h timeframe**. Enters trending markets when RSI pulls back in the direction of the trend. Uses **ADX** to confirm trend strength and **DMI (+DI/-DI)** to confirm trend direction. The RSI pullback provides precise entry timing within established trends.

## Target Market & Venue

- **Asset**: BTC/USDC (Hyperliquid) or BTC/USDT (Binance Futures)
- **Venue**: Hyperliquid Perps (default) or Binance Futures
- **Timeframe**: 1h candles

## Strategy Logic

### Entry Rules

| Condition | Long | Short |
|-----------|------|-------|
| ADX trend strength | ADX(14) > 20 | ADX(14) > 20 |
| RSI range | 35-50 | 50-65 |
| RSI momentum | Rising vs 2 bars ago | Falling vs 2 bars ago |
| Candle direction | Close > Open (bullish) | Close < Open (bearish) |
| DMI direction | +DI > -DI | -DI > +DI |

All conditions must be true simultaneously.

### Exit Rules

1. **Trailing stop**: ATR-based at `entry +/- (ATR * 3.0)`
2. **Max hold time**: 16 bars (16 hours) — prevents stale positions

### Position Sizing

Risk-per-trade model: `size = (equity * risk_pct) / |entry - stop|`
- Default: 2% risk per trade
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
| RSI Period | 14 | Momentum measurement |
| ADX Period | 14 | Trend strength |
| ADX Threshold | 20 | Min trend strength |
| RSI Long Range | 35-50 | Pullback zone for longs |
| RSI Short Range | 50-65 | Pullback zone for shorts |
| Stop ATR Mult | 3.0 | Trailing stop distance |
| Max Hold Bars | 16 | 16 hours max |
| Risk % | 0.02 | 2% per trade |

## How to Configure

1. Copy `.env.example` to `.env` and fill in your API keys
2. Edit `config.yaml` to adjust strategy parameters (see Parameter Reference below)
3. Set `enabled: false` in `config.yaml` to disable this strategy

## How to Run

### Testnet (default)

```bash
# Run this strategy only
python -m bots.python.btc_momentum_1h

# Run this strategy only
python -m bots.python.btc_momentum_1h
```

### Mainnet

```bash
# In .env:
BOT_TESTNET=false

# Then run as above
python -m bots.python.btc_momentum_1h
```

## How to Backtest

Write a backtest that imports the strategy:

```python
from bots.python.btc_momentum_1h.strategy import BTCMomentum1hStrategy

strategy = BTCMomentum1hStrategy(
    rsi_period=14,
    adx_threshold=20,
    max_hold_bars=16,
)
df_ind = strategy.compute_indicators(df_1h, df_daily)
```

## Risk Warnings

- **Momentum strategies can reverse quickly**. A pullback entry can become a full reversal.
- ADX > 20 does NOT guarantee the trend will continue — it only confirms strength at entry time.
- The 16-bar max hold prevents stale positions but may cut winners short in strong trends.
- RSI ranges (35-50 for longs, 50-65 for shorts) are calibrated for BTC. Different assets need different ranges.
- This bot trades both long AND short. Shorting has theoretically unlimited risk.
- **Never risk more than you can afford to lose.** Start on testnet.

## Parameter Reference

| Parameter | What It Does | Valid Range | Default | Risk Implication |
|-----------|-------------|-------------|---------|-----------------|
| `rsi_period` | RSI calculation period | 5-30 | 14 | Lower = more sensitive signals |
| `adx_period` | ADX calculation period | 5-30 | 14 | Lower = faster trend detection |
| `adx_threshold` | Min ADX to confirm trend | 10-40 | 20 | Higher = fewer, higher-quality trades |
| `rsi_long_min` | Min RSI for long entry | 20-45 | 35 | Lower = deeper pullbacks required |
| `rsi_long_max` | Max RSI for long entry | 40-60 | 50 | Higher = earlier entries |
| `rsi_short_min` | Min RSI for short entry | 40-60 | 50 | Lower = earlier short entries |
| `rsi_short_max` | Max RSI for short entry | 55-80 | 65 | Higher = stronger pullbacks required |
| `atr_period` | ATR calculation period | 5-30 | 14 | Lower = more responsive stops |
| `stop_atr_mult` | Stop distance multiplier | 1.0-10.0 | 3.0 | Lower = tighter stops, more exits |
| `max_hold_bars` | Max bars to hold position | 4-48 | 16 | Lower = faster turnover |
| `risk_pct` | Risk per trade | 0.005-0.10 | 0.02 | Higher = larger positions, bigger drawdowns |
| `warmup_bars` | Bars before trading starts | 50-500 | 220 | Prevents unreliable indicator values |
| `enabled` | Enable/disable strategy | true/false | true | Set false to run solo |
