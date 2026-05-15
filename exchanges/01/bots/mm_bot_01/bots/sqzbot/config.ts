// Squeeze Momentum Bot configuration
// Strategy: BB/KC squeeze detection + MACD direction + trend/volume filters
// Timeframe: 30m candles (aggregated from 1m)
// Best pair: HYPE — backtested 180d: +3.6%, +0.127R, 22.6% WR, 31 trades
// Risk: 1% per trade, 1:3 RR ratio
export interface SQZBotConfig {
  readonly symbol: string;

  // ── Bollinger Bands ────────────────────────────────────────────────────────
  readonly bbPeriod: number;       // SMA period (20)
  readonly bbMult: number;         // StdDev multiplier (2.0)

  // ── Keltner Channel ────────────────────────────────────────────────────────
  readonly kcPeriod: number;       // EMA period (20)
  readonly kcAtrPeriod: number;    // ATR period for KC width (10)
  readonly kcMult: number;         // ATR multiplier (1.5)

  // ── MACD (direction) ──────────────────────────────────────────────────────
  readonly macdFast: number;       // Fast EMA (12)
  readonly macdSlow: number;       // Slow EMA (26)
  readonly macdSignal: number;     // Signal EMA (9)

  // ── Filters ────────────────────────────────────────────────────────────────
  readonly emaTrendPeriod: number; // EMA for trend filter (200)
  readonly rsiPeriod: number;      // RSI period (14)
  readonly volumeSmaPeriod: number;// Volume SMA period (20)
  readonly minSqzBars: number;     // Minimum consecutive squeeze bars (6)
  readonly minVolumeRatio: number; // Min volume / SMA(volume) ratio (1.2)
  readonly rsiMin: number;         // RSI floor for entries (30)
  readonly rsiMax: number;         // RSI ceiling for entries (70)

  // ── Candle timeframe ─────────────────────────────────────────────────────
  readonly candleMinutes: number;  // Aggregation timeframe (15 = 15m candles)

  // ── Risk management ────────────────────────────────────────────────────────
  readonly riskPct: number;        // Fraction of balance risked per trade (0.01 = 1%)
  readonly atrSlPeriod: number;    // ATR period for SL sizing (14)
  readonly slAtrMult: number;      // SL = slAtrMult × ATR from entry (1.5)
  readonly rrRatio: number;        // TP = rrRatio × SL distance (3.0)
  readonly breakEvenAtR: number;   // Move SL to BE at this R-multiple (1.0)
  readonly timeExitBars: number;   // Force close after N bars (45)
  readonly takerFeePct: number;    // Taker fee per side (0.00035)

  // ── Execution ──────────────────────────────────────────────────────────────
  readonly slippageBps: number;
  readonly statusIntervalMs: number;
  readonly positionSyncIntervalMs: number;
  readonly candleHistoryLength: number;
  readonly fillVerifyDelayMs: number;
}

export const DEFAULT_SQZ_CONFIG: Omit<SQZBotConfig, "symbol"> = {
  // Bollinger Bands
  bbPeriod: 20,
  bbMult: 2.0,

  // Keltner Channel
  kcPeriod: 20,
  kcAtrPeriod: 10,
  kcMult: 1.5,

  // MACD
  macdFast: 12,
  macdSlow: 26,
  macdSignal: 9,

  // Filters
  emaTrendPeriod: 200,
  rsiPeriod: 14,
  volumeSmaPeriod: 20,
  minSqzBars: 6,
  minVolumeRatio: 1.2,
  rsiMin: 30,
  rsiMax: 70,

  // 30-minute candles (aggregated from 1m)
  candleMinutes: 30,

  // Risk: 1% per trade, 1:3 RR
  riskPct: 0.01,
  atrSlPeriod: 14,
  slAtrMult: 1.5,
  rrRatio: 3.0,
  breakEvenAtR: 1.0,
  timeExitBars: 45,
  takerFeePct: 0.00035,

  // Execution
  slippageBps: 50,
  statusIntervalMs: 5_000,
  positionSyncIntervalMs: 10_000,
  candleHistoryLength: 300,
  fillVerifyDelayMs: 2_000,
};
