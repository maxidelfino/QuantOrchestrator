// MarketMaker configuration
export interface MarketMakerConfig {
  readonly symbol: string;
  readonly spreadBps: number;
  readonly takeProfitBps: number;
  readonly orderSizeUsd: number;
  readonly closeThresholdUsd: number;
  readonly warmupSeconds: number;
  readonly updateThrottleMs: number;
  readonly orderSyncIntervalMs: number;
  readonly statusIntervalMs: number;
  readonly fairPriceWindowMs: number;
  readonly positionSyncIntervalMs: number;
  // ── Risk management ──────────────────────────────────────────────
  // Maximum total capital deployed (bid + ask combined). With $18 USDC,
  // keeping this at ~80% leaves a buffer for fees and adverse moves.
  readonly maxCapitalUsd: number;
  // Stop quoting if unrealized loss on current position exceeds this.
  readonly maxPositionLossUsd: number;
  // Minimum spread to maintain even in close mode — ensures we never
  // accidentally cross the spread and become a taker (paying higher fees).
  readonly minSpreadBps: number;
}

// Default config — tuned for ~$18 USDC capital on mainnet.
// Key decisions:
//   spreadBps=12  → 0.12% spread. Typical maker fee on 01 is ~0.02%,
//                   so each round trip costs ~0.04%. 12bps gives a
//                   comfortable buffer before fees eat the profit.
//   orderSizeUsd=5 → small enough to survive several fills without
//                   hitting closeThreshold, but meaningful enough to
//                   accumulate volume.
//   closeThresholdUsd=8 → if our net position reaches $8, we flip to
//                   close-only mode to limit inventory risk.
//   maxCapitalUsd=14 → never commit more than ~80% of $18 at once.
export const DEFAULT_CONFIG: Omit<MarketMakerConfig, "symbol"> = {
  spreadBps: 12,
  takeProfitBps: 6,
  orderSizeUsd: 5,
  closeThresholdUsd: 8,
  warmupSeconds: 10,
  updateThrottleMs: 200,
  orderSyncIntervalMs: 3000,
  statusIntervalMs: 2000,
  fairPriceWindowMs: 5 * 60 * 1000,
  positionSyncIntervalMs: 5000,
  // Risk controls
  maxCapitalUsd: 14,
  maxPositionLossUsd: 2,
  minSpreadBps: 3,
};
