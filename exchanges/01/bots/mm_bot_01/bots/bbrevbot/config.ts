/**
 * BB Mean Reversion Bot configuration.
 * Strategy: Enter at BB band extremes, exit at BB middle (SMA).
 * Designed for 30m candles (aggregated from 1m Binance data).
 */
export interface BBRevBotConfig {
  readonly symbol: string;

  // ── Bollinger Bands ────────────────────────────────────────────────────────
  readonly bbPeriod: number;       // SMA period (20)
  readonly bbMult: number;         // StdDev multiplier (2.0)

  // ── Trend filter ───────────────────────────────────────────────────────────
  readonly emaTrendPeriod: number; // EMA for trend filter (50). 0 = disabled.

  // ── Candle timeframe ───────────────────────────────────────────────────────
  readonly candleMinutes: number;  // Aggregation timeframe (30 = 30m candles)

  // ── Risk management ────────────────────────────────────────────────────────
  readonly riskPct: number;        // Fraction of balance risked per trade (0.01)
  readonly atrPeriod: number;      // ATR period for SL sizing (14)
  readonly slAtrMult: number;      // SL = slAtrMult × ATR from entry (1.5)
  readonly timeExitBars: number;   // Force close after N bars (30)
  readonly takerFeePct: number;    // Taker fee per side (0.00035)

  // ── Execution ──────────────────────────────────────────────────────────────
  readonly slippageBps: number;
  readonly statusIntervalMs: number;
  readonly positionSyncIntervalMs: number;
  readonly candleHistoryLength: number;
  readonly fillVerifyDelayMs: number;
}

export const DEFAULT_BBREV_CONFIG: Omit<BBRevBotConfig, "symbol"> = {
  // Bollinger Bands
  bbPeriod: 20,
  bbMult: 2.0,

  // Trend filter
  emaTrendPeriod: 50,

  // 30-minute candles
  candleMinutes: 30,

  // Risk: 1% per trade, no fixed TP (indicator exit), SL as safety net
  riskPct: 0.01,
  atrPeriod: 14,
  slAtrMult: 1.5,
  timeExitBars: 30,
  takerFeePct: 0.00035,

  // Execution
  slippageBps: 50,
  statusIntervalMs: 5_000,
  positionSyncIntervalMs: 10_000,
  candleHistoryLength: 500, // More history for 30m aggregation warmup
  fillVerifyDelayMs: 2_000,
};
