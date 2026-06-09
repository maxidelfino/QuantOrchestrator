/**
 * BB Mean Reversion Indicator
 *
 * Detects price touching Bollinger Band extremes for mean reversion entries.
 * Uses EMA trend filter to only trade dips in uptrends / rallies in downtrends.
 *
 * Components:
 *   - Bollinger Bands (SMA20, 2.0σ) — volatility envelope
 *   - EMA(50) — trend filter (optional)
 *   - ATR(14) — SL sizing
 *
 * Entry: price <= BB lower + uptrend → LONG
 *        price >= BB upper + downtrend → SHORT
 * Exit:  price crosses BB middle (SMA) → mean reversion complete
 */

import type { Candle } from "../../pricing/candles.js";

export interface BBRevState {
  bbMid: number;
  bbUpper: number;
  bbLower: number;
  ema: number | null;
  atr: number;
  price: number;
  longSignal: boolean;
  shortSignal: boolean;
  exitLong: boolean;   // signal to close long (price >= bbMid)
  exitShort: boolean;  // signal to close short (price <= bbMid)
}

class EMA {
  private value = 0;
  private count = 0;
  private sum = 0;
  private ready = false;
  private readonly alpha: number;

  constructor(private readonly period: number) {
    this.alpha = 2 / (period + 1);
  }

  update(x: number): number | null {
    if (!this.ready) {
      this.sum += x;
      this.count++;
      if (this.count >= this.period) {
        this.value = this.sum / this.period;
        this.ready = true;
        return this.value;
      }
      return null;
    }
    this.value = this.alpha * x + (1 - this.alpha) * this.value;
    return this.value;
  }
}

class WildersRMA {
  private value = 0;
  private count = 0;
  private sum = 0;
  private ready = false;

  constructor(private readonly period: number) {}

  update(x: number): number | null {
    if (!this.ready) {
      this.sum += x;
      this.count++;
      if (this.count >= this.period) {
        this.value = this.sum / this.period;
        this.ready = true;
      }
      return this.ready ? this.value : null;
    }
    this.value = (this.value * (this.period - 1) + x) / this.period;
    return this.value;
  }
}

class RollingBB {
  private readonly buffer: number[] = [];

  constructor(
    private readonly period: number,
    private readonly mult: number,
  ) {}

  update(x: number): { sma: number; upper: number; lower: number } | null {
    this.buffer.push(x);
    if (this.buffer.length > this.period) this.buffer.shift();
    if (this.buffer.length < this.period) return null;

    const sma = this.buffer.reduce((a, b) => a + b, 0) / this.period;
    // Population StdDev (divide by N, matches TradingView)
    const variance = this.buffer.reduce((a, b) => a + (b - sma) ** 2, 0) / this.period;
    const stdDev = Math.sqrt(variance);

    return {
      sma,
      upper: sma + this.mult * stdDev,
      lower: sma - this.mult * stdDev,
    };
  }
}

export class BBRevIndicator {
  private readonly bb: RollingBB;
  private readonly ema: EMA | null;
  private readonly atr: WildersRMA;
  private prevClose = 0;

  constructor(
    bbPeriod: number,
    bbMult: number,
    emaPeriod: number,
    atrPeriod: number,
  ) {
    this.bb = new RollingBB(bbPeriod, bbMult);
    this.ema = emaPeriod > 0 ? new EMA(emaPeriod) : null;
    this.atr = new WildersRMA(atrPeriod);
  }

  update(candle: Candle): BBRevState | null {
    const close = candle.close;

    // ATR
    const tr = this.prevClose === 0
      ? candle.high - candle.low
      : Math.max(
          candle.high - candle.low,
          Math.abs(candle.high - this.prevClose),
          Math.abs(candle.low - this.prevClose),
        );
    this.prevClose = close;
    const atrVal = this.atr.update(tr);

    // BB
    const bbVal = this.bb.update(close);

    // EMA trend filter
    const emaVal = this.ema?.update(close) ?? null;

    if (atrVal === null || bbVal === null) return null;
    if (this.ema && emaVal === null) return null;

    const trendLong = emaVal !== null ? close > emaVal : true;
    const trendShort = emaVal !== null ? close < emaVal : true;

    return {
      bbMid: bbVal.sma,
      bbUpper: bbVal.upper,
      bbLower: bbVal.lower,
      ema: emaVal,
      atr: atrVal,
      price: close,
      longSignal: close <= bbVal.lower && trendLong,
      shortSignal: close >= bbVal.upper && trendShort,
      exitLong: close >= bbVal.sma,
      exitShort: close <= bbVal.sma,
    };
  }
}
