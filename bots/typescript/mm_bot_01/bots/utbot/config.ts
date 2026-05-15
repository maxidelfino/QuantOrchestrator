// Donchian Breakout Bot configuration (replaces UT Bot)
// Strategy: Donchian Channel breakout + ADX trend filter
// Timeframe: 1h candles (aggregated from 1m)
// Edge: +0.056R per trade on BTC (backtested 180d)
export interface UTBotConfig {
  readonly symbol: string;

  // ── Donchian Channel ──────────────────────────────────────────────────────
  readonly entryPeriod: number;   // Channel lookback for breakout (50)
  readonly minAdx: number;        // Minimum ADX for trend confirmation (20)
  readonly adxPeriod: number;     // ADX smoothing period (14)

  // ── Candle timeframe ──────────────────────────────────────────────────────
  readonly candleMinutes: number; // Aggregation timeframe (60 = 1h candles)

  // ── Risk management ───────────────────────────────────────────────────────
  readonly riskPct: number;       // Fraction of balance risked per trade (0.01 = 1%)
  readonly atrSlPeriod: number;   // ATR period for SL sizing (14)
  readonly slAtrMult: number;     // SL = slAtrMult × ATR from entry (1.5)
  readonly rrRatio: number;       // TP = rrRatio × SL distance (3.0)
  readonly breakEvenAtR: number;  // Move SL to BE at this R-multiple (1.0)
  readonly timeExitBars: number;  // Force close after N bars (60)
  readonly takerFeePct: number;   // Taker fee per side (0.00035)

  // ── Execution ─────────────────────────────────────────────────────────────
  readonly slippageBps: number;
  readonly statusIntervalMs: number;
  readonly positionSyncIntervalMs: number;
  readonly candleHistoryLength: number;
  readonly fillVerifyDelayMs: number;
}

export const DEFAULT_UTBOT_CONFIG: Omit<UTBotConfig, "symbol"> = {
  // Donchian Channel: 50-period breakout
  entryPeriod: 50,
  minAdx: 20,
  adxPeriod: 14,

  // 1-hour candles (aggregated from 1m)
  candleMinutes: 60,

  // Risk: 1% per trade, 1:3 RR
  riskPct: 0.01,
  atrSlPeriod: 14,
  slAtrMult: 1.5,
  rrRatio: 3.0,
  breakEvenAtR: 1.0,
  timeExitBars: 60,
  takerFeePct: 0.00035,

  // Execution
  slippageBps: 50,
  statusIntervalMs: 5_000,
  positionSyncIntervalMs: 10_000,
  candleHistoryLength: 500,
  fillVerifyDelayMs: 2_000,
};
