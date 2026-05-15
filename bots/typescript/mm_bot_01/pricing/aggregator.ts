/**
 * Real-time candle aggregator: converts 1m candles into N-minute candles.
 * Used by bots that trade on higher timeframes (15m, 30m) while still
 * receiving 1m data from Binance WebSocket.
 */

import type { Candle } from "./candles.js";

export class CandleAggregator {
  private current: Candle | null = null;
  private readonly intervalMs: number;

  constructor(private readonly minutes: number) {
    this.intervalMs = minutes * 60_000;
  }

  /**
   * Feed a closed 1m candle. Returns the completed N-minute candle
   * when a new bucket starts, or null if still accumulating.
   */
  update(candle1m: Candle): Candle | null {
    const bucketTime = candle1m.time - (candle1m.time % this.intervalMs);

    if (this.current && this.current.time === bucketTime) {
      // Same bucket: update OHLCV
      this.current.high = Math.max(this.current.high, candle1m.high);
      this.current.low = Math.min(this.current.low, candle1m.low);
      this.current.close = candle1m.close;
      this.current.volume += candle1m.volume;
      return null;
    }

    // New bucket: emit previous if exists, start new
    let completed: Candle | null = null;
    if (this.current) {
      completed = { ...this.current };
    }

    this.current = {
      time: bucketTime,
      open: candle1m.open,
      high: candle1m.high,
      low: candle1m.low,
      close: candle1m.close,
      volume: candle1m.volume,
    };

    return completed;
  }

  /**
   * Aggregate historical 1m candles into N-minute candles.
   * Returns all completed candles. Sets internal state to the last
   * (potentially incomplete) bucket so streaming continues correctly.
   */
  warmup(candles1m: readonly Candle[]): Candle[] {
    const result: Candle[] = [];
    for (const c of candles1m) {
      const bucketTime = c.time - (c.time % this.intervalMs);
      const last = result[result.length - 1];

      if (last && last.time === bucketTime) {
        last.high = Math.max(last.high, c.high);
        last.low = Math.min(last.low, c.low);
        last.close = c.close;
        last.volume += c.volume;
      } else {
        result.push({
          time: bucketTime,
          open: c.open,
          high: c.high,
          low: c.low,
          close: c.close,
          volume: c.volume,
        });
      }
    }

    // Keep last bucket as current (may be incomplete)
    if (result.length > 0) {
      this.current = { ...result[result.length - 1] };
      result.pop();
    }

    return result;
  }
}
