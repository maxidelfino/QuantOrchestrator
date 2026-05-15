/**
 * Squeeze Momentum Indicator
 *
 * Detects volatility compression (BB inside KC) and fires entry signals
 * when the squeeze releases, using MACD histogram for direction.
 *
 * Components:
 *   - Bollinger Bands (SMA20, 2.0σ) — volatility envelope
 *   - Keltner Channel (EMA20, 1.5×ATR10) — trend envelope
 *   - Squeeze = BB inside KC (compression → imminent expansion)
 *   - MACD (12,26,9) histogram — momentum direction
 *   - EMA(200) — trend filter (only trade with trend)
 *   - RSI(14) — avoid extremes
 *   - Volume SMA(20) — confirmation
 *   - ATR(14) — SL sizing
 */

import type { Candle } from "../../pricing/candles.js";

export type { Candle };

// ── Output ───────────────────────────────────────────────────────────────────
export interface SQZState {
  squeezeOn: boolean;     // BB currently inside KC
  squeezeFired: boolean;  // squeeze just released (was on, now off)
  sqzBars: number;        // consecutive bars in squeeze
  macdHist: number;       // MACD histogram value
  ema200: number;
  rsi: number;
  atr: number;            // ATR(14) for SL sizing
  volumeRatio: number;    // current volume / SMA(volume)
  price: number;
  longSignal: boolean;
  shortSignal: boolean;
}

// ── EMA ──────────────────────────────────────────────────────────────────────
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

  isReady(): boolean { return this.ready; }
  getValue(): number { return this.value; }
}

// ── Wilder's RMA ─────────────────────────────────────────────────────────────
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

// ── Rolling buffer with SMA + StdDev (for Bollinger Bands) ──────────────────
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
    const variance = this.buffer.reduce((a, b) => a + (b - sma) ** 2, 0) / this.period;
    const stdDev = Math.sqrt(variance);

    return {
      sma,
      upper: sma + this.mult * stdDev,
      lower: sma - this.mult * stdDev,
    };
  }
}

// ── Rolling SMA ──────────────────────────────────────────────────────────────
class RollingSMA {
  private readonly buffer: number[] = [];

  constructor(private readonly period: number) {}

  update(x: number): number | null {
    this.buffer.push(x);
    if (this.buffer.length > this.period) this.buffer.shift();
    if (this.buffer.length < this.period) return null;
    return this.buffer.reduce((a, b) => a + b, 0) / this.period;
  }
}

// ── Squeeze Momentum Indicator ───────────────────────────────────────────────
export class SQZIndicator {
  // Bollinger Bands
  private readonly bb: RollingBB;

  // Keltner Channel
  private readonly kcEma: EMA;
  private readonly kcAtr: WildersRMA;
  private readonly kcMult: number;

  // MACD
  private readonly macdFast: EMA;
  private readonly macdSlow: EMA;
  private readonly macdSignalEma: EMA;

  // Trend filter
  private readonly ema200: EMA;

  // RSI
  private readonly rsiGainRMA: WildersRMA;
  private readonly rsiLossRMA: WildersRMA;
  private prevCloseRsi = 0;
  private rsiInit = false;

  // ATR for SL sizing (separate from KC ATR)
  private readonly atrSL: WildersRMA;
  private prevCloseAtr = 0;

  // Volume filter
  private readonly volumeSMA: RollingSMA;

  // Squeeze state
  private prevSqzOn = false;
  private sqzConsecutive = 0;
  private prevKcClose = 0; // for TR calc (KC ATR)

  // Config
  private readonly minSqzBars: number;
  private readonly minVolRatio: number;
  private readonly rsiMin: number;
  private readonly rsiMax: number;

  constructor(
    bbPeriod: number,
    bbMult: number,
    kcPeriod: number,
    kcAtrPeriod: number,
    kcMult: number,
    macdFast: number,
    macdSlow: number,
    macdSignal: number,
    emaTrendPeriod: number,
    rsiPeriod: number,
    atrSlPeriod: number,
    volumeSmaPeriod: number,
    minSqzBars: number,
    minVolRatio: number,
    rsiMin: number,
    rsiMax: number,
  ) {
    this.bb = new RollingBB(bbPeriod, bbMult);
    this.kcEma = new EMA(kcPeriod);
    this.kcAtr = new WildersRMA(kcAtrPeriod);
    this.kcMult = kcMult;
    this.macdFast = new EMA(macdFast);
    this.macdSlow = new EMA(macdSlow);
    this.macdSignalEma = new EMA(macdSignal);
    this.ema200 = new EMA(emaTrendPeriod);
    this.rsiGainRMA = new WildersRMA(rsiPeriod);
    this.rsiLossRMA = new WildersRMA(rsiPeriod);
    this.atrSL = new WildersRMA(atrSlPeriod);
    this.volumeSMA = new RollingSMA(volumeSmaPeriod);
    this.minSqzBars = minSqzBars;
    this.minVolRatio = minVolRatio;
    this.rsiMin = rsiMin;
    this.rsiMax = rsiMax;
  }

  /**
   * Process one closed 1m candle. Returns null during warmup.
   */
  update(candle: Candle): SQZState | null {
    const close = candle.close;

    // ── ATR for SL sizing (14-period) ─────────────────────────────────────
    const trSL = this.prevCloseAtr === 0
      ? candle.high - candle.low
      : Math.max(
          candle.high - candle.low,
          Math.abs(candle.high - this.prevCloseAtr),
          Math.abs(candle.low - this.prevCloseAtr),
        );
    this.prevCloseAtr = close;
    const atrSlVal = this.atrSL.update(trSL);

    // ── ATR for Keltner Channel (10-period) ───────────────────────────────
    const trKC = this.prevKcClose === 0
      ? candle.high - candle.low
      : Math.max(
          candle.high - candle.low,
          Math.abs(candle.high - this.prevKcClose),
          Math.abs(candle.low - this.prevKcClose),
        );
    this.prevKcClose = close;
    const kcAtrVal = this.kcAtr.update(trKC);

    // ── Bollinger Bands ───────────────────────────────────────────────────
    const bbVal = this.bb.update(close);

    // ── Keltner Channel ───────────────────────────────────────────────────
    const kcMid = this.kcEma.update(close);

    // ── MACD ──────────────────────────────────────────────────────────────
    const macdFastVal = this.macdFast.update(close);
    const macdSlowVal = this.macdSlow.update(close);
    let macdHist: number | null = null;
    if (macdFastVal !== null && macdSlowVal !== null) {
      const macdLine = macdFastVal - macdSlowVal;
      const signalVal = this.macdSignalEma.update(macdLine);
      if (signalVal !== null) {
        macdHist = macdLine - signalVal;
      }
    }

    // ── EMA 200 (trend) ───────────────────────────────────────────────────
    const ema200Val = this.ema200.update(close);

    // ── RSI ───────────────────────────────────────────────────────────────
    let rsiVal: number | null = null;
    if (!this.rsiInit) {
      this.prevCloseRsi = close;
      this.rsiInit = true;
    } else {
      const change = close - this.prevCloseRsi;
      this.prevCloseRsi = close;
      const gain = Math.max(change, 0);
      const loss = Math.max(-change, 0);
      const avgGain = this.rsiGainRMA.update(gain);
      const avgLoss = this.rsiLossRMA.update(loss);
      if (avgGain !== null && avgLoss !== null) {
        rsiVal = avgLoss === 0 ? 100 : 100 - (100 / (1 + avgGain / avgLoss));
      }
    }

    // ── Volume ────────────────────────────────────────────────────────────
    const volSmaVal = this.volumeSMA.update(candle.volume);

    // ── Check readiness ───────────────────────────────────────────────────
    if (
      bbVal === null ||
      kcMid === null ||
      kcAtrVal === null ||
      macdHist === null ||
      ema200Val === null ||
      rsiVal === null ||
      atrSlVal === null ||
      volSmaVal === null
    ) {
      return null;
    }

    // ── Squeeze detection ─────────────────────────────────────────────────
    const kcUpper = kcMid + this.kcMult * kcAtrVal;
    const kcLower = kcMid - this.kcMult * kcAtrVal;
    const sqzOn = bbVal.lower > kcLower && bbVal.upper < kcUpper;

    if (sqzOn) {
      this.sqzConsecutive++;
    } else {
      this.sqzConsecutive = 0;
    }

    // Squeeze fires = was squeezing long enough AND just released
    const squeezeFired = this.prevSqzOn && !sqzOn && this.sqzConsecutive === 0;
    const sqzBarsAtFire = this.prevSqzOn ? this.sqzConsecutive : 0;
    // We need to track how many bars the previous squeeze lasted
    // Since sqzConsecutive resets to 0 when sqz turns off, we use prevSqzBars
    const prevSqzBarsForSignal = squeezeFired ? this.lastSqzLength : 0;

    // Track squeeze length before it ends
    if (sqzOn) {
      this.lastSqzLength = this.sqzConsecutive;
    }

    this.prevSqzOn = sqzOn;

    // ── Volume ratio ──────────────────────────────────────────────────────
    const volumeRatio = volSmaVal > 0 ? candle.volume / volSmaVal : 0;

    // ── Entry signals ─────────────────────────────────────────────────────
    const longSignal =
      squeezeFired &&
      prevSqzBarsForSignal >= this.minSqzBars &&
      macdHist > 0 &&
      close > ema200Val &&
      rsiVal > this.rsiMin &&
      rsiVal < this.rsiMax &&
      volumeRatio >= this.minVolRatio;

    const shortSignal =
      squeezeFired &&
      prevSqzBarsForSignal >= this.minSqzBars &&
      macdHist < 0 &&
      close < ema200Val &&
      rsiVal > this.rsiMin &&
      rsiVal < this.rsiMax &&
      volumeRatio >= this.minVolRatio;

    return {
      squeezeOn: sqzOn,
      squeezeFired,
      sqzBars: sqzOn ? this.sqzConsecutive : prevSqzBarsForSignal,
      macdHist,
      ema200: ema200Val,
      rsi: rsiVal,
      atr: atrSlVal,
      volumeRatio,
      price: close,
      longSignal,
      shortSignal,
    };
  }

  // Track how long the last squeeze was (before it releases)
  private lastSqzLength = 0;
}
