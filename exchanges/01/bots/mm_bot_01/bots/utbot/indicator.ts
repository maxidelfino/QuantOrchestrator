/**
 * Donchian Breakout Indicator (replaces UT Bot indicator)
 *
 * Donchian Channel: N-period high/low defines the channel.
 * Breakout: close > channel high = LONG, close < channel low = SHORT.
 * ADX filter: only trade when ADX > threshold (trending market).
 * ATR: used for stop-loss sizing.
 */

import type { Candle } from "../../pricing/candles.js";

export type { Candle };

export interface DonchianState {
  price: number;
  channelHigh: number;
  channelLow: number;
  channelMid: number;
  atr: number;
  adx: number;
  longSignal: boolean;
  shortSignal: boolean;
}

// ── Wilder's RMA ──────────────────────────────────────────────────────────────
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
  isReady(): boolean { return this.ready; }
}

export class DonchianIndicator {
  private highBuffer: number[] = [];
  private lowBuffer: number[] = [];

  private atrRMA: WildersRMA;
  private adxTR_RMA: WildersRMA;
  private adxPlusDM_RMA: WildersRMA;
  private adxMinusDM_RMA: WildersRMA;
  private adxSmoothRMA: WildersRMA;

  private prevClose = 0;
  private prevHigh = 0;
  private prevLow = 0;

  constructor(
    private readonly entryPeriod: number,
    private readonly minAdx: number,
    private readonly atrPeriod: number,
    private readonly adxPeriod: number,
  ) {
    this.atrRMA = new WildersRMA(atrPeriod);
    this.adxTR_RMA = new WildersRMA(adxPeriod);
    this.adxPlusDM_RMA = new WildersRMA(adxPeriod);
    this.adxMinusDM_RMA = new WildersRMA(adxPeriod);
    this.adxSmoothRMA = new WildersRMA(adxPeriod);
  }

  /**
   * Process one closed candle.
   * Returns null while warming up (need entryPeriod + adxPeriod bars).
   */
  update(candle: Candle): DonchianState | null {
    const close = candle.close;

    // ── True Range + ATR ──────────────────────────────────────────────────
    const tr = this.prevClose === 0
      ? candle.high - candle.low
      : Math.max(
          candle.high - candle.low,
          Math.abs(candle.high - this.prevClose),
          Math.abs(candle.low - this.prevClose),
        );

    const atrVal = this.atrRMA.update(tr);

    // ── ADX ───────────────────────────────────────────────────────────────
    let adxVal: number | null = null;
    if (this.prevHigh !== 0) {
      const plusDM = Math.max(candle.high - this.prevHigh, 0);
      const minusDM = Math.max(this.prevLow - candle.low, 0);
      const adjPlusDM = plusDM > minusDM ? plusDM : 0;
      const adjMinusDM = minusDM > plusDM ? minusDM : 0;

      const smoothTR = this.adxTR_RMA.update(tr);
      const smoothPlus = this.adxPlusDM_RMA.update(adjPlusDM);
      const smoothMinus = this.adxMinusDM_RMA.update(adjMinusDM);

      if (smoothTR !== null && smoothPlus !== null && smoothMinus !== null && smoothTR > 0) {
        const plusDI = (smoothPlus / smoothTR) * 100;
        const minusDI = (smoothMinus / smoothTR) * 100;
        const sum = plusDI + minusDI;
        const dx = sum > 0 ? (Math.abs(plusDI - minusDI) / sum) * 100 : 0;
        adxVal = this.adxSmoothRMA.update(dx);
      }
    }

    this.prevClose = close;
    this.prevHigh = candle.high;
    this.prevLow = candle.low;

    // ── Donchian Channel ──────────────────────────────────────────────────
    this.highBuffer.push(candle.high);
    this.lowBuffer.push(candle.low);
    if (this.highBuffer.length > this.entryPeriod) this.highBuffer.shift();
    if (this.lowBuffer.length > this.entryPeriod) this.lowBuffer.shift();

    // Need enough data
    if (atrVal === null || adxVal === null || this.highBuffer.length < this.entryPeriod) {
      return null;
    }

    // Channel from previous N bars (exclude current bar)
    const prevHighs = this.highBuffer.slice(0, -1);
    const prevLows = this.lowBuffer.slice(0, -1);
    if (prevHighs.length < this.entryPeriod - 1) return null;

    const channelHigh = Math.max(...prevHighs);
    const channelLow = Math.min(...prevLows);
    const channelMid = (channelHigh + channelLow) / 2;

    // ── Signals ───────────────────────────────────────────────────────────
    const adxOk = adxVal >= this.minAdx;

    return {
      price: close,
      channelHigh,
      channelLow,
      channelMid,
      atr: atrVal,
      adx: adxVal,
      longSignal: adxOk && close > channelHigh,
      shortSignal: adxOk && close < channelLow,
    };
  }
}
