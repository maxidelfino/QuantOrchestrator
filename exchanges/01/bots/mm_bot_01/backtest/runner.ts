/**
 * Backtest runner for all 3 directional bots.
 * Usage: tsx src/backtest/runner.ts <bot> <symbol> [days]
 * Example: tsx src/backtest/runner.ts all BTC 30
 */

import { aggregateCandles, fetchCandles } from "./data.js";
import {
  type BacktestConfig,
  type BacktestStrategy,
  type EntrySignal,
  printResult,
  runBacktest,
} from "./engine.js";
import type { Candle } from "./data.js";

// ══════════════════════════════════════════════════════════════════════════════
// Shared indicator helpers (mirrors production code exactly)
// ══════════════════════════════════════════════════════════════════════════════

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
  getValue(): number { return this.value; }
}

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

class RollingBB {
  private readonly buffer: number[] = [];
  constructor(private readonly period: number, private readonly mult: number) {}
  update(x: number): { sma: number; upper: number; lower: number } | null {
    this.buffer.push(x);
    if (this.buffer.length > this.period) this.buffer.shift();
    if (this.buffer.length < this.period) return null;
    const sma = this.buffer.reduce((a, b) => a + b, 0) / this.period;
    const variance = this.buffer.reduce((a, b) => a + (b - sma) ** 2, 0) / this.period;
    return { sma, upper: sma + this.mult * Math.sqrt(variance), lower: sma - this.mult * Math.sqrt(variance) };
  }
}

function calcTR(candle: Candle, prevClose: number): number {
  if (prevClose === 0) return candle.high - candle.low;
  return Math.max(
    candle.high - candle.low,
    Math.abs(candle.high - prevClose),
    Math.abs(candle.low - prevClose),
  );
}

// ══════════════════════════════════════════════════════════════════════════════
// Donchian Breakout Strategy (replaces UT Bot)
// Entry: close breaks above/below N-period high/low + ADX >= threshold
// ══════════════════════════════════════════════════════════════════════════════

class UTBotStrategy implements BacktestStrategy {
  private highBuffer: number[] = [];
  private lowBuffer: number[] = [];
  private atrRMA!: WildersRMA;
  private adxTR_RMA!: WildersRMA;
  private adxPlusDM_RMA!: WildersRMA;
  private adxMinusDM_RMA!: WildersRMA;
  private adxSmoothRMA!: WildersRMA;
  private prevClose = 0;
  private prevHigh = 0;
  private prevLow = 0;

  constructor(
    private readonly period: number = 50,
    private readonly minAdx: number = 20,
  ) { this.reset(); }

  reset(): void {
    this.highBuffer = [];
    this.lowBuffer = [];
    this.atrRMA = new WildersRMA(14);
    this.adxTR_RMA = new WildersRMA(14);
    this.adxPlusDM_RMA = new WildersRMA(14);
    this.adxMinusDM_RMA = new WildersRMA(14);
    this.adxSmoothRMA = new WildersRMA(14);
    this.prevClose = 0;
    this.prevHigh = 0;
    this.prevLow = 0;
  }

  onCandle(candle: Candle): EntrySignal {
    const tr = calcTR(candle, this.prevClose);
    const atrVal = this.atrRMA.update(tr);

    // ADX
    let adxVal: number | null = null;
    if (this.prevHigh !== 0) {
      const pDM = Math.max(candle.high - this.prevHigh, 0);
      const mDM = Math.max(this.prevLow - candle.low, 0);
      const apDM = pDM > mDM ? pDM : 0;
      const amDM = mDM > pDM ? mDM : 0;
      const sTR = this.adxTR_RMA.update(tr);
      const spDM = this.adxPlusDM_RMA.update(apDM);
      const smDM = this.adxMinusDM_RMA.update(amDM);
      if (sTR !== null && spDM !== null && smDM !== null && sTR > 0) {
        const pDI = (spDM / sTR) * 100;
        const mDI = (smDM / sTR) * 100;
        const sum = pDI + mDI;
        const dx = sum > 0 ? (Math.abs(pDI - mDI) / sum) * 100 : 0;
        adxVal = this.adxSmoothRMA.update(dx);
      }
    }

    this.prevClose = candle.close;
    this.prevHigh = candle.high;
    this.prevLow = candle.low;

    this.highBuffer.push(candle.high);
    this.lowBuffer.push(candle.low);
    if (this.highBuffer.length > this.period) this.highBuffer.shift();
    if (this.lowBuffer.length > this.period) this.lowBuffer.shift();

    if (atrVal === null || this.highBuffer.length < this.period) {
      return { direction: "none", atr: atrVal ?? 0 };
    }

    const prevHighs = this.highBuffer.slice(0, -1);
    const prevLows = this.lowBuffer.slice(0, -1);
    if (prevHighs.length < this.period - 1) return { direction: "none", atr: atrVal };

    const channelHigh = Math.max(...prevHighs);
    const channelLow = Math.min(...prevLows);

    // ADX filter
    if (this.minAdx > 0 && (adxVal === null || adxVal < this.minAdx)) {
      return { direction: "none", atr: atrVal };
    }

    if (candle.close > channelHigh) return { direction: "long", atr: atrVal };
    if (candle.close < channelLow) return { direction: "short", atr: atrVal };
    return { direction: "none", atr: atrVal };
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// Peak Bot Strategy
// ══════════════════════════════════════════════════════════════════════════════

class PeakBotStrategy implements BacktestStrategy {
  private ema9!: EMA;
  private ema21!: EMA;
  private ema50!: EMA;
  private ema3ofEma9!: EMA;
  private ema3ofEma21!: EMA;
  private rsiGainRMA!: WildersRMA;
  private rsiLossRMA!: WildersRMA;
  private atrRMA!: WildersRMA;
  private volatilitySMA!: RollingSMA;
  private adxSmoothRMA!: WildersRMA;
  private adxPlusDM_RMA!: WildersRMA;
  private adxMinusDM_RMA!: WildersRMA;
  private adxTR_RMA!: WildersRMA;

  private prevCloseRsi = 0;
  private rsiInit = false;
  private prevCloseAtr = 0;
  private prevEma3of9 = 0;
  private prevEma3of21 = 0;
  private prevHigh = 0;
  private prevLow = 0;

  constructor() { this.reset(); }

  reset(): void {
    this.ema9 = new EMA(9);
    this.ema21 = new EMA(21);
    this.ema50 = new EMA(50);
    this.ema3ofEma9 = new EMA(3);
    this.ema3ofEma21 = new EMA(3);
    this.rsiGainRMA = new WildersRMA(14);
    this.rsiLossRMA = new WildersRMA(14);
    this.atrRMA = new WildersRMA(14);
    this.volatilitySMA = new RollingSMA(20);
    this.adxSmoothRMA = new WildersRMA(14);
    this.adxPlusDM_RMA = new WildersRMA(14);
    this.adxMinusDM_RMA = new WildersRMA(14);
    this.adxTR_RMA = new WildersRMA(14);
    this.prevCloseRsi = 0;
    this.rsiInit = false;
    this.prevCloseAtr = 0;
    this.prevEma3of9 = 0;
    this.prevEma3of21 = 0;
    this.prevHigh = 0;
    this.prevLow = 0;
  }

  onCandle(candle: Candle): EntrySignal {
    const close = candle.close;
    const none: EntrySignal = { direction: "none", atr: 0 };

    // ATR
    const tr = calcTR(candle, this.prevCloseAtr);
    this.prevCloseAtr = close;
    const atrVal = this.atrRMA.update(tr);
    let volSma: number | null = null;
    if (atrVal !== null) volSma = this.volatilitySMA.update(atrVal);

    // ADX
    let adxVal: number | null = null;
    if (this.prevHigh !== 0) {
      const pDM = Math.max(candle.high - this.prevHigh, 0);
      const mDM = Math.max(this.prevLow - candle.low, 0);
      const apDM = pDM > mDM ? pDM : 0;
      const amDM = mDM > pDM ? mDM : 0;
      const sTR = this.adxTR_RMA.update(tr);
      const spDM = this.adxPlusDM_RMA.update(apDM);
      const smDM = this.adxMinusDM_RMA.update(amDM);
      if (sTR !== null && spDM !== null && smDM !== null && sTR > 0) {
        const pDI = (spDM / sTR) * 100;
        const mDI = (smDM / sTR) * 100;
        const sum = pDI + mDI;
        const dx = sum > 0 ? (Math.abs(pDI - mDI) / sum) * 100 : 0;
        adxVal = this.adxSmoothRMA.update(dx);
      }
    }
    this.prevHigh = candle.high;
    this.prevLow = candle.low;

    // EMAs
    const e9 = this.ema9.update(close);
    const e21 = this.ema21.update(close);
    const e50 = this.ema50.update(close);

    // RSI
    let rsi: number | null = null;
    if (!this.rsiInit) { this.prevCloseRsi = close; this.rsiInit = true; }
    else {
      const ch = close - this.prevCloseRsi;
      this.prevCloseRsi = close;
      const ag = this.rsiGainRMA.update(Math.max(ch, 0));
      const al = this.rsiLossRMA.update(Math.max(-ch, 0));
      if (ag !== null && al !== null) rsi = al === 0 ? 100 : 100 - (100 / (1 + ag / al));
    }

    if (atrVal === null || volSma === null || e9 === null || e21 === null || rsi === null) {
      if (e9 !== null) { const v = this.ema3ofEma9.update(e9); if (v !== null) this.prevEma3of9 = v; }
      if (e21 !== null) { const v = this.ema3ofEma21.update(e21); if (v !== null) this.prevEma3of21 = v; }
      return { direction: "none", atr: atrVal ?? 0 };
    }

    const volOk = atrVal > volSma;
    let longDir = false, shortDir = false;
    if (this.prevEma3of21 !== 0) longDir = e21 > this.prevEma3of21;
    if (this.prevEma3of9 !== 0) shortDir = e9 > this.prevEma3of9;

    const v21 = this.ema3ofEma21.update(e21);
    const v9 = this.ema3ofEma9.update(e9);
    if (v21 !== null) this.prevEma3of21 = v21;
    if (v9 !== null) this.prevEma3of9 = v9;

    // Filters: 24h mode, volatility, ADX > 20
    let tradeOk = volOk;
    if (adxVal !== null && adxVal < 20) tradeOk = false;

    const priceLong = e50 !== null ? close > e50 : true;
    const priceShort = e50 !== null ? close < e50 : true;

    if (!tradeOk) return { direction: "none", atr: atrVal };

    const longSig = close > e9 && e9 > e21 && longDir && shortDir && rsi > 50 && priceLong;
    const shortSig = close < e9 && e9 < e21 && !longDir && !shortDir && rsi < 50 && priceShort;

    if (longSig) return { direction: "long", atr: atrVal };
    if (shortSig) return { direction: "short", atr: atrVal };
    return { direction: "none", atr: atrVal };
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// SQZ Bot Strategy
// ══════════════════════════════════════════════════════════════════════════════

class SQZBotStrategy implements BacktestStrategy {
  private bb!: RollingBB;
  private kcEma!: EMA;
  private kcAtr!: WildersRMA;
  private macdFast!: EMA;
  private macdSlow!: EMA;
  private macdSignal!: EMA;
  private ema200!: EMA;
  private rsiGainRMA!: WildersRMA;
  private rsiLossRMA!: WildersRMA;
  private atrSL!: WildersRMA;
  private volSMA!: RollingSMA;

  private prevCloseAtr = 0;
  private prevCloseKc = 0;
  private prevCloseRsi = 0;
  private rsiInit = false;
  private prevSqzOn = false;
  private sqzConsecutive = 0;
  private lastSqzLength = 0;

  constructor() { this.reset(); }

  reset(): void {
    this.bb = new RollingBB(20, 2.0);
    this.kcEma = new EMA(20);
    this.kcAtr = new WildersRMA(10);
    this.macdFast = new EMA(12);
    this.macdSlow = new EMA(26);
    this.macdSignal = new EMA(9);
    this.ema200 = new EMA(200);
    this.rsiGainRMA = new WildersRMA(14);
    this.rsiLossRMA = new WildersRMA(14);
    this.atrSL = new WildersRMA(14);
    this.volSMA = new RollingSMA(20);
    this.prevCloseAtr = 0;
    this.prevCloseKc = 0;
    this.prevCloseRsi = 0;
    this.rsiInit = false;
    this.prevSqzOn = false;
    this.sqzConsecutive = 0;
    this.lastSqzLength = 0;
  }

  onCandle(candle: Candle): EntrySignal {
    const close = candle.close;
    const none: EntrySignal = { direction: "none", atr: 0 };

    // ATR for SL
    const trSL = calcTR(candle, this.prevCloseAtr);
    this.prevCloseAtr = close;
    const atrSlVal = this.atrSL.update(trSL);

    // ATR for KC
    const trKC = calcTR(candle, this.prevCloseKc);
    this.prevCloseKc = close;
    const kcAtrVal = this.kcAtr.update(trKC);

    // BB
    const bbVal = this.bb.update(close);
    // KC
    const kcMid = this.kcEma.update(close);

    // MACD
    const mf = this.macdFast.update(close);
    const ms = this.macdSlow.update(close);
    let macdHist: number | null = null;
    if (mf !== null && ms !== null) {
      const line = mf - ms;
      const sig = this.macdSignal.update(line);
      if (sig !== null) macdHist = line - sig;
    }

    // EMA200
    const e200 = this.ema200.update(close);

    // RSI
    let rsi: number | null = null;
    if (!this.rsiInit) { this.prevCloseRsi = close; this.rsiInit = true; }
    else {
      const ch = close - this.prevCloseRsi;
      this.prevCloseRsi = close;
      const ag = this.rsiGainRMA.update(Math.max(ch, 0));
      const al = this.rsiLossRMA.update(Math.max(-ch, 0));
      if (ag !== null && al !== null) rsi = al === 0 ? 100 : 100 - (100 / (1 + ag / al));
    }

    // Volume
    const volSmaVal = this.volSMA.update(candle.volume);

    if (bbVal === null || kcMid === null || kcAtrVal === null || macdHist === null || e200 === null || rsi === null || atrSlVal === null || volSmaVal === null) {
      return none;
    }

    // Squeeze detection
    const kcUpper = kcMid + 1.5 * kcAtrVal;
    const kcLower = kcMid - 1.5 * kcAtrVal;
    const sqzOn = bbVal.lower > kcLower && bbVal.upper < kcUpper;

    if (sqzOn) {
      this.sqzConsecutive++;
      this.lastSqzLength = this.sqzConsecutive;
    } else {
      this.sqzConsecutive = 0;
    }

    const fired = this.prevSqzOn && !sqzOn;
    const prevLen = fired ? this.lastSqzLength : 0;
    this.prevSqzOn = sqzOn;

    if (!fired || prevLen < 6) return { direction: "none", atr: atrSlVal };

    // Volume ratio
    const volRatio = volSmaVal > 0 ? candle.volume / volSmaVal : 0;
    if (volRatio < 1.2) return { direction: "none", atr: atrSlVal };

    // RSI filter
    if (rsi < 30 || rsi > 70) return { direction: "none", atr: atrSlVal };

    // Direction from MACD + EMA200 trend
    if (macdHist > 0 && close > e200) return { direction: "long", atr: atrSlVal };
    if (macdHist < 0 && close < e200) return { direction: "short", atr: atrSlVal };

    return { direction: "none", atr: atrSlVal };
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// Supertrend Strategy (cleaner UT Bot replacement)
// Uses Supertrend indicator + ADX trend filter + MACD momentum confirmation
// ══════════════════════════════════════════════════════════════════════════════

class SupertrendStrategy implements BacktestStrategy {
  private atrRMA!: WildersRMA;
  private adxTR_RMA!: WildersRMA;
  private adxPlusDM_RMA!: WildersRMA;
  private adxMinusDM_RMA!: WildersRMA;
  private adxSmoothRMA!: WildersRMA;
  private macdFast!: EMA;
  private macdSlow!: EMA;
  private macdSignal!: EMA;

  private prevClose = 0;
  private prevHigh = 0;
  private prevLow = 0;
  private prevSupertrend = 0;
  private prevDirection = 1; // 1=up, -1=down
  private prevUpperBand = 0;
  private prevLowerBand = 0;

  constructor(
    private readonly atrPeriod: number = 10,
    private readonly atrMult: number = 3.0,
    private readonly minAdx: number = 20,
    private readonly useMacd: boolean = true,
  ) { this.reset(); }

  reset(): void {
    this.atrRMA = new WildersRMA(this.atrPeriod);
    this.adxTR_RMA = new WildersRMA(14);
    this.adxPlusDM_RMA = new WildersRMA(14);
    this.adxMinusDM_RMA = new WildersRMA(14);
    this.adxSmoothRMA = new WildersRMA(14);
    this.macdFast = new EMA(12);
    this.macdSlow = new EMA(26);
    this.macdSignal = new EMA(9);
    this.prevClose = 0;
    this.prevHigh = 0;
    this.prevLow = 0;
    this.prevSupertrend = 0;
    this.prevDirection = 1;
    this.prevUpperBand = 0;
    this.prevLowerBand = 0;
  }

  onCandle(candle: Candle): EntrySignal {
    const close = candle.close;
    const hl2 = (candle.high + candle.low) / 2;
    const tr = calcTR(candle, this.prevClose);

    // ATR
    const atrVal = this.atrRMA.update(tr);

    // ADX
    let adxVal: number | null = null;
    if (this.prevHigh !== 0) {
      const pDM = Math.max(candle.high - this.prevHigh, 0);
      const mDM = Math.max(this.prevLow - candle.low, 0);
      const apDM = pDM > mDM ? pDM : 0;
      const amDM = mDM > pDM ? mDM : 0;
      const sTR = this.adxTR_RMA.update(tr);
      const spDM = this.adxPlusDM_RMA.update(apDM);
      const smDM = this.adxMinusDM_RMA.update(amDM);
      if (sTR !== null && spDM !== null && smDM !== null && sTR > 0) {
        const pDI = (spDM / sTR) * 100;
        const mDI = (smDM / sTR) * 100;
        const sum = pDI + mDI;
        const dx = sum > 0 ? (Math.abs(pDI - mDI) / sum) * 100 : 0;
        adxVal = this.adxSmoothRMA.update(dx);
      }
    }

    // MACD
    const mf = this.macdFast.update(close);
    const ms = this.macdSlow.update(close);
    let macdHist: number | null = null;
    if (mf !== null && ms !== null) {
      const line = mf - ms;
      const sig = this.macdSignal.update(line);
      if (sig !== null) macdHist = line - sig;
    }

    this.prevClose = close;
    this.prevHigh = candle.high;
    this.prevLow = candle.low;

    if (atrVal === null) return { direction: "none", atr: 0 };

    // Supertrend calculation
    const basicUpper = hl2 + this.atrMult * atrVal;
    const basicLower = hl2 - this.atrMult * atrVal;

    const upperBand = (basicUpper < this.prevUpperBand || this.prevClose > this.prevUpperBand)
      ? basicUpper : this.prevUpperBand;
    const lowerBand = (basicLower > this.prevLowerBand || this.prevClose < this.prevLowerBand)
      ? basicLower : this.prevLowerBand;

    let direction: number;
    if (this.prevSupertrend === this.prevUpperBand) {
      direction = close > upperBand ? 1 : -1;
    } else {
      direction = close < lowerBand ? -1 : 1;
    }

    const supertrend = direction === 1 ? lowerBand : upperBand;

    // Detect direction CHANGE (signal)
    const dirChanged = direction !== this.prevDirection;
    const prevDir = this.prevDirection;

    this.prevSupertrend = supertrend;
    this.prevDirection = direction;
    this.prevUpperBand = upperBand;
    this.prevLowerBand = lowerBand;

    if (!dirChanged) return { direction: "none", atr: atrVal };

    // ADX filter: only trade in trending markets
    if (this.minAdx > 0 && (adxVal === null || adxVal < this.minAdx)) {
      return { direction: "none", atr: atrVal };
    }

    // MACD confirmation
    if (this.useMacd && macdHist !== null) {
      if (direction === 1 && macdHist <= 0) return { direction: "none", atr: atrVal };
      if (direction === -1 && macdHist >= 0) return { direction: "none", atr: atrVal };
    }

    if (direction === 1) return { direction: "long", atr: atrVal };
    if (direction === -1) return { direction: "short", atr: atrVal };
    return { direction: "none", atr: atrVal };
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// Donchian Breakout Strategy (Turtle Trading inspired)
// Entry: price breaks N-period high/low with ADX confirmation
// ══════════════════════════════════════════════════════════════════════════════

class DonchianBreakoutStrategy implements BacktestStrategy {
  private highBuffer: number[] = [];
  private lowBuffer: number[] = [];
  private atrRMA!: WildersRMA;
  private adxTR_RMA!: WildersRMA;
  private adxPlusDM_RMA!: WildersRMA;
  private adxMinusDM_RMA!: WildersRMA;
  private adxSmoothRMA!: WildersRMA;
  private prevClose = 0;
  private prevHigh = 0;
  private prevLow = 0;

  constructor(
    private readonly period: number = 20,
    private readonly minAdx: number = 20,
  ) { this.reset(); }

  reset(): void {
    this.highBuffer = [];
    this.lowBuffer = [];
    this.atrRMA = new WildersRMA(14);
    this.adxTR_RMA = new WildersRMA(14);
    this.adxPlusDM_RMA = new WildersRMA(14);
    this.adxMinusDM_RMA = new WildersRMA(14);
    this.adxSmoothRMA = new WildersRMA(14);
    this.prevClose = 0;
    this.prevHigh = 0;
    this.prevLow = 0;
  }

  onCandle(candle: Candle): EntrySignal {
    const tr = calcTR(candle, this.prevClose);
    const atrVal = this.atrRMA.update(tr);

    // ADX
    let adxVal: number | null = null;
    if (this.prevHigh !== 0) {
      const pDM = Math.max(candle.high - this.prevHigh, 0);
      const mDM = Math.max(this.prevLow - candle.low, 0);
      const apDM = pDM > mDM ? pDM : 0;
      const amDM = mDM > pDM ? mDM : 0;
      const sTR = this.adxTR_RMA.update(tr);
      const spDM = this.adxPlusDM_RMA.update(apDM);
      const smDM = this.adxMinusDM_RMA.update(amDM);
      if (sTR !== null && spDM !== null && smDM !== null && sTR > 0) {
        const pDI = (spDM / sTR) * 100;
        const mDI = (smDM / sTR) * 100;
        const sum = pDI + mDI;
        const dx = sum > 0 ? (Math.abs(pDI - mDI) / sum) * 100 : 0;
        adxVal = this.adxSmoothRMA.update(dx);
      }
    }

    this.prevClose = candle.close;
    this.prevHigh = candle.high;
    this.prevLow = candle.low;

    // Track highs/lows
    this.highBuffer.push(candle.high);
    this.lowBuffer.push(candle.low);
    if (this.highBuffer.length > this.period) this.highBuffer.shift();
    if (this.lowBuffer.length > this.period) this.lowBuffer.shift();

    if (atrVal === null || this.highBuffer.length < this.period) {
      return { direction: "none", atr: atrVal ?? 0 };
    }

    // Donchian channel (previous N bars, excluding current)
    const prevHighs = this.highBuffer.slice(0, -1);
    const prevLows = this.lowBuffer.slice(0, -1);
    if (prevHighs.length < this.period - 1) return { direction: "none", atr: atrVal };

    const channelHigh = Math.max(...prevHighs);
    const channelLow = Math.min(...prevLows);

    // ADX filter
    if (this.minAdx > 0 && (adxVal === null || adxVal < this.minAdx)) {
      return { direction: "none", atr: atrVal };
    }

    // Breakout signals
    if (candle.close > channelHigh) return { direction: "long", atr: atrVal };
    if (candle.close < channelLow) return { direction: "short", atr: atrVal };
    return { direction: "none", atr: atrVal };
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// EMA Crossover + ADX + MACD Confirmation
// Entry: EMA fast crosses slow + ADX > threshold + MACD agrees
// ══════════════════════════════════════════════════════════════════════════════

class EMACrossADXStrategy implements BacktestStrategy {
  private emaFast!: EMA;
  private emaSlow!: EMA;
  private atrRMA!: WildersRMA;
  private adxTR_RMA!: WildersRMA;
  private adxPlusDM_RMA!: WildersRMA;
  private adxMinusDM_RMA!: WildersRMA;
  private adxSmoothRMA!: WildersRMA;
  private macdFast!: EMA;
  private macdSlow!: EMA;
  private macdSignal!: EMA;

  private prevClose = 0;
  private prevHigh = 0;
  private prevLow = 0;
  private prevFastEma = 0;
  private prevSlowEma = 0;

  constructor(
    private readonly fastPeriod: number = 9,
    private readonly slowPeriod: number = 21,
    private readonly minAdx: number = 25,
  ) { this.reset(); }

  reset(): void {
    this.emaFast = new EMA(this.fastPeriod);
    this.emaSlow = new EMA(this.slowPeriod);
    this.atrRMA = new WildersRMA(14);
    this.adxTR_RMA = new WildersRMA(14);
    this.adxPlusDM_RMA = new WildersRMA(14);
    this.adxMinusDM_RMA = new WildersRMA(14);
    this.adxSmoothRMA = new WildersRMA(14);
    this.macdFast = new EMA(12);
    this.macdSlow = new EMA(26);
    this.macdSignal = new EMA(9);
    this.prevClose = 0;
    this.prevHigh = 0;
    this.prevLow = 0;
    this.prevFastEma = 0;
    this.prevSlowEma = 0;
  }

  onCandle(candle: Candle): EntrySignal {
    const close = candle.close;
    const tr = calcTR(candle, this.prevClose);
    const atrVal = this.atrRMA.update(tr);

    const fastVal = this.emaFast.update(close);
    const slowVal = this.emaSlow.update(close);

    // ADX
    let adxVal: number | null = null;
    if (this.prevHigh !== 0) {
      const pDM = Math.max(candle.high - this.prevHigh, 0);
      const mDM = Math.max(this.prevLow - candle.low, 0);
      const apDM = pDM > mDM ? pDM : 0;
      const amDM = mDM > pDM ? mDM : 0;
      const sTR = this.adxTR_RMA.update(tr);
      const spDM = this.adxPlusDM_RMA.update(apDM);
      const smDM = this.adxMinusDM_RMA.update(amDM);
      if (sTR !== null && spDM !== null && smDM !== null && sTR > 0) {
        const pDI = (spDM / sTR) * 100;
        const mDI = (smDM / sTR) * 100;
        const sum = pDI + mDI;
        const dx = sum > 0 ? (Math.abs(pDI - mDI) / sum) * 100 : 0;
        adxVal = this.adxSmoothRMA.update(dx);
      }
    }

    // MACD
    const mf = this.macdFast.update(close);
    const ms = this.macdSlow.update(close);
    let macdHist: number | null = null;
    if (mf !== null && ms !== null) {
      const line = mf - ms;
      const sig = this.macdSignal.update(line);
      if (sig !== null) macdHist = line - sig;
    }

    this.prevClose = close;
    this.prevHigh = candle.high;
    this.prevLow = candle.low;

    if (atrVal === null || fastVal === null || slowVal === null) {
      if (fastVal !== null) this.prevFastEma = fastVal;
      if (slowVal !== null) this.prevSlowEma = slowVal;
      return { direction: "none", atr: atrVal ?? 0 };
    }

    // Detect EMA crossover
    const bullCross = this.prevFastEma <= this.prevSlowEma && fastVal > slowVal;
    const bearCross = this.prevFastEma >= this.prevSlowEma && fastVal < slowVal;

    this.prevFastEma = fastVal;
    this.prevSlowEma = slowVal;

    if (!bullCross && !bearCross) return { direction: "none", atr: atrVal };

    // ADX filter
    if (this.minAdx > 0 && (adxVal === null || adxVal < this.minAdx)) {
      return { direction: "none", atr: atrVal };
    }

    // MACD confirmation
    if (macdHist !== null) {
      if (bullCross && macdHist <= 0) return { direction: "none", atr: atrVal };
      if (bearCross && macdHist >= 0) return { direction: "none", atr: atrVal };
    }

    if (bullCross) return { direction: "long", atr: atrVal };
    if (bearCross) return { direction: "short", atr: atrVal };
    return { direction: "none", atr: atrVal };
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// RSI Momentum Strategy
// Entry: RSI crosses above/below threshold with EMA trend filter
// Designed for trending markets — ride momentum, not mean revert
// ══════════════════════════════════════════════════════════════════════════════

class RSIMomentumStrategy implements BacktestStrategy {
  private rsiGainRMA!: WildersRMA;
  private rsiLossRMA!: WildersRMA;
  private atrRMA!: WildersRMA;
  private ema!: EMA;
  private prevCloseRsi = 0;
  private rsiInit = false;
  private prevCloseAtr = 0;
  private prevRsi = 50;

  constructor(
    private readonly rsiPeriod: number = 14,
    private readonly rsiBuyLevel: number = 55, // RSI crossing above → long
    private readonly rsiSellLevel: number = 45, // RSI crossing below → short
    private readonly emaPeriod: number = 50,
  ) { this.reset(); }

  reset(): void {
    this.rsiGainRMA = new WildersRMA(this.rsiPeriod);
    this.rsiLossRMA = new WildersRMA(this.rsiPeriod);
    this.atrRMA = new WildersRMA(14);
    this.ema = new EMA(this.emaPeriod);
    this.prevCloseRsi = 0;
    this.rsiInit = false;
    this.prevCloseAtr = 0;
    this.prevRsi = 50;
  }

  onCandle(candle: Candle): EntrySignal {
    const close = candle.close;
    const tr = calcTR(candle, this.prevCloseAtr);
    this.prevCloseAtr = close;
    const atrVal = this.atrRMA.update(tr);
    const emaVal = this.ema.update(close);

    let rsi: number | null = null;
    if (!this.rsiInit) { this.prevCloseRsi = close; this.rsiInit = true; }
    else {
      const ch = close - this.prevCloseRsi;
      this.prevCloseRsi = close;
      const ag = this.rsiGainRMA.update(Math.max(ch, 0));
      const al = this.rsiLossRMA.update(Math.max(-ch, 0));
      if (ag !== null && al !== null) rsi = al === 0 ? 100 : 100 - (100 / (1 + ag / al));
    }

    if (atrVal === null || rsi === null || emaVal === null) {
      if (rsi !== null) this.prevRsi = rsi;
      return { direction: "none", atr: atrVal ?? 0 };
    }

    // Detect RSI crossover
    const bullCross = this.prevRsi <= this.rsiBuyLevel && rsi > this.rsiBuyLevel;
    const bearCross = this.prevRsi >= this.rsiSellLevel && rsi < this.rsiSellLevel;
    this.prevRsi = rsi;

    if (!bullCross && !bearCross) return { direction: "none", atr: atrVal };

    // EMA trend filter
    if (bullCross && close > emaVal) return { direction: "long", atr: atrVal };
    if (bearCross && close < emaVal) return { direction: "short", atr: atrVal };
    return { direction: "none", atr: atrVal };
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// Donchian Turtle Strategy (entry on breakout, trailing stop exit)
// Uses N-period high/low for entry AND N/2-period low/high for exit (trailing)
// This lets winners run instead of fixed TP
// ══════════════════════════════════════════════════════════════════════════════

class TurtleStrategy implements BacktestStrategy {
  private highBuffer: number[] = [];
  private lowBuffer: number[] = [];
  private exitHighBuffer: number[] = [];
  private exitLowBuffer: number[] = [];
  private atrRMA!: WildersRMA;
  private prevClose = 0;

  constructor(
    private readonly entryPeriod: number = 20,
    private readonly exitPeriod: number = 10,
  ) { this.reset(); }

  reset(): void {
    this.highBuffer = [];
    this.lowBuffer = [];
    this.exitHighBuffer = [];
    this.exitLowBuffer = [];
    this.atrRMA = new WildersRMA(14);
    this.prevClose = 0;
  }

  onCandle(candle: Candle): EntrySignal {
    const tr = calcTR(candle, this.prevClose);
    this.prevClose = candle.close;
    const atrVal = this.atrRMA.update(tr);

    this.highBuffer.push(candle.high);
    this.lowBuffer.push(candle.low);
    this.exitHighBuffer.push(candle.high);
    this.exitLowBuffer.push(candle.low);
    if (this.highBuffer.length > this.entryPeriod) this.highBuffer.shift();
    if (this.lowBuffer.length > this.entryPeriod) this.lowBuffer.shift();
    if (this.exitHighBuffer.length > this.exitPeriod) this.exitHighBuffer.shift();
    if (this.exitLowBuffer.length > this.exitPeriod) this.exitLowBuffer.shift();

    if (atrVal === null || this.highBuffer.length < this.entryPeriod) {
      return { direction: "none", atr: atrVal ?? 0 };
    }

    const prevHighs = this.highBuffer.slice(0, -1);
    const prevLows = this.lowBuffer.slice(0, -1);
    const channelHigh = Math.max(...prevHighs);
    const channelLow = Math.min(...prevLows);

    if (candle.close > channelHigh) return { direction: "long", atr: atrVal };
    if (candle.close < channelLow) return { direction: "short", atr: atrVal };
    return { direction: "none", atr: atrVal };
  }

  // Trailing exit: exit long when price breaks below N/2 period low, exit short when above N/2 period high
  shouldExit(candle: Candle, side: "long" | "short"): boolean {
    if (this.exitLowBuffer.length < this.exitPeriod) return false;
    const prevExitLows = this.exitLowBuffer.slice(0, -1);
    const prevExitHighs = this.exitHighBuffer.slice(0, -1);
    if (prevExitLows.length < 2) return false;

    if (side === "long") {
      const exitLevel = Math.min(...prevExitLows);
      return candle.close < exitLevel;
    } else {
      const exitLevel = Math.max(...prevExitHighs);
      return candle.close > exitLevel;
    }
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// Pullback Strategy: enter on pullback within established trend
// Uses EMA for trend direction + RSI pullback zone for timing
// ══════════════════════════════════════════════════════════════════════════════

class PullbackStrategy implements BacktestStrategy {
  private emaFast!: EMA;
  private emaSlow!: EMA;
  private rsiGainRMA!: WildersRMA;
  private rsiLossRMA!: WildersRMA;
  private atrRMA!: WildersRMA;
  private adxTR_RMA!: WildersRMA;
  private adxPlusDM_RMA!: WildersRMA;
  private adxMinusDM_RMA!: WildersRMA;
  private adxSmoothRMA!: WildersRMA;
  private prevCloseRsi = 0;
  private rsiInit = false;
  private prevCloseAtr = 0;
  private prevHigh = 0;
  private prevLow = 0;

  constructor(
    private readonly fastPeriod: number = 20,
    private readonly slowPeriod: number = 50,
    private readonly rsiPullbackLong: number = 40,  // RSI below this = oversold pullback in uptrend
    private readonly rsiPullbackShort: number = 60, // RSI above this = overbought pullback in downtrend
    private readonly minAdx: number = 20,
  ) { this.reset(); }

  reset(): void {
    this.emaFast = new EMA(this.fastPeriod);
    this.emaSlow = new EMA(this.slowPeriod);
    this.rsiGainRMA = new WildersRMA(14);
    this.rsiLossRMA = new WildersRMA(14);
    this.atrRMA = new WildersRMA(14);
    this.adxTR_RMA = new WildersRMA(14);
    this.adxPlusDM_RMA = new WildersRMA(14);
    this.adxMinusDM_RMA = new WildersRMA(14);
    this.adxSmoothRMA = new WildersRMA(14);
    this.prevCloseRsi = 0;
    this.rsiInit = false;
    this.prevCloseAtr = 0;
    this.prevHigh = 0;
    this.prevLow = 0;
  }

  onCandle(candle: Candle): EntrySignal {
    const close = candle.close;
    const tr = calcTR(candle, this.prevCloseAtr);
    this.prevCloseAtr = close;
    const atrVal = this.atrRMA.update(tr);
    const fastVal = this.emaFast.update(close);
    const slowVal = this.emaSlow.update(close);

    // ADX
    let adxVal: number | null = null;
    if (this.prevHigh !== 0) {
      const pDM = Math.max(candle.high - this.prevHigh, 0);
      const mDM = Math.max(this.prevLow - candle.low, 0);
      const apDM = pDM > mDM ? pDM : 0;
      const amDM = mDM > pDM ? mDM : 0;
      const sTR = this.adxTR_RMA.update(tr);
      const spDM = this.adxPlusDM_RMA.update(apDM);
      const smDM = this.adxMinusDM_RMA.update(amDM);
      if (sTR !== null && spDM !== null && smDM !== null && sTR > 0) {
        const pDI = (spDM / sTR) * 100;
        const mDI = (smDM / sTR) * 100;
        const sum = pDI + mDI;
        const dx = sum > 0 ? (Math.abs(pDI - mDI) / sum) * 100 : 0;
        adxVal = this.adxSmoothRMA.update(dx);
      }
    }
    this.prevHigh = candle.high;
    this.prevLow = candle.low;

    // RSI
    let rsi: number | null = null;
    if (!this.rsiInit) { this.prevCloseRsi = close; this.rsiInit = true; }
    else {
      const ch = close - this.prevCloseRsi;
      this.prevCloseRsi = close;
      const ag = this.rsiGainRMA.update(Math.max(ch, 0));
      const al = this.rsiLossRMA.update(Math.max(-ch, 0));
      if (ag !== null && al !== null) rsi = al === 0 ? 100 : 100 - (100 / (1 + ag / al));
    }

    if (atrVal === null || rsi === null || fastVal === null || slowVal === null) {
      return { direction: "none", atr: atrVal ?? 0 };
    }

    // ADX filter
    if (this.minAdx > 0 && (adxVal === null || adxVal < this.minAdx)) {
      return { direction: "none", atr: atrVal };
    }

    // Uptrend: fast EMA > slow EMA, price > fast EMA → pullback = RSI drops below threshold
    const uptrend = fastVal > slowVal && close > slowVal;
    const downtrend = fastVal < slowVal && close < slowVal;

    if (uptrend && rsi < this.rsiPullbackLong) return { direction: "long", atr: atrVal };
    if (downtrend && rsi > this.rsiPullbackShort) return { direction: "short", atr: atrVal };
    return { direction: "none", atr: atrVal };
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// Mean Reversion Bot Strategy (RSI2)
// ══════════════════════════════════════════════════════════════════════════════

class MeanRevStrategy implements BacktestStrategy {
  private rsiGainRMA!: WildersRMA;
  private rsiLossRMA!: WildersRMA;
  private atrRMA!: WildersRMA;
  private ema50!: EMA;

  private prevCloseRsi = 0;
  private rsiInit = false;
  private prevCloseAtr = 0;

  constructor(
    private readonly rsiPeriod: number = 2,
    private readonly rsiBuyThreshold: number = 10,
    private readonly rsiSellThreshold: number = 90,
    private readonly emaPeriod: number = 50,
  ) { this.reset(); }

  reset(): void {
    this.rsiGainRMA = new WildersRMA(this.rsiPeriod);
    this.rsiLossRMA = new WildersRMA(this.rsiPeriod);
    this.atrRMA = new WildersRMA(14);
    this.ema50 = new EMA(this.emaPeriod);
    this.prevCloseRsi = 0;
    this.rsiInit = false;
    this.prevCloseAtr = 0;
  }

  onCandle(candle: Candle): EntrySignal {
    const close = candle.close;

    // ATR
    const tr = calcTR(candle, this.prevCloseAtr);
    this.prevCloseAtr = close;
    const atrVal = this.atrRMA.update(tr);

    // EMA trend filter
    const emaVal = this.ema50.update(close);

    // RSI(2)
    let rsi: number | null = null;
    if (!this.rsiInit) { this.prevCloseRsi = close; this.rsiInit = true; }
    else {
      const ch = close - this.prevCloseRsi;
      this.prevCloseRsi = close;
      const ag = this.rsiGainRMA.update(Math.max(ch, 0));
      const al = this.rsiLossRMA.update(Math.max(-ch, 0));
      if (ag !== null && al !== null) rsi = al === 0 ? 100 : 100 - (100 / (1 + ag / al));
    }

    if (atrVal === null || rsi === null || emaVal === null) {
      return { direction: "none", atr: atrVal ?? 0 };
    }

    // Mean reversion: extreme RSI(2) with trend filter
    if (rsi < this.rsiBuyThreshold && close > emaVal) return { direction: "long", atr: atrVal };
    if (rsi > this.rsiSellThreshold && close < emaVal) return { direction: "short", atr: atrVal };

    return { direction: "none", atr: atrVal };
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// BB Mean Reversion Strategy (indicator-based exit)
// Entry: price touches BB band. Exit: price crosses BB middle.
// ══════════════════════════════════════════════════════════════════════════════

class BBRevStrategy implements BacktestStrategy {
  private bb!: RollingBB;
  private atrRMA!: WildersRMA;
  private ema!: EMA | null;
  private prevCloseAtr = 0;
  private lastBBMid = 0;

  constructor(
    private readonly bbPeriod: number = 20,
    private readonly bbMult: number = 2.0,
    private readonly emaPeriod: number = 0, // 0 = no trend filter
  ) { this.reset(); }

  reset(): void {
    this.bb = new RollingBB(this.bbPeriod, this.bbMult);
    this.atrRMA = new WildersRMA(14);
    this.ema = this.emaPeriod > 0 ? new EMA(this.emaPeriod) : null;
    this.prevCloseAtr = 0;
    this.lastBBMid = 0;
  }

  onCandle(candle: Candle): EntrySignal {
    const close = candle.close;
    const tr = calcTR(candle, this.prevCloseAtr);
    this.prevCloseAtr = close;
    const atrVal = this.atrRMA.update(tr);
    const emaVal = this.ema?.update(close) ?? null;
    const bbVal = this.bb.update(close);

    if (atrVal === null || bbVal === null) return { direction: "none", atr: atrVal ?? 0 };
    if (this.ema && emaVal === null) return { direction: "none", atr: atrVal };
    this.lastBBMid = bbVal.sma;

    const trendLong = emaVal !== null ? close > emaVal : true;
    const trendShort = emaVal !== null ? close < emaVal : true;

    if (close <= bbVal.lower && trendLong) return { direction: "long", atr: atrVal };
    if (close >= bbVal.upper && trendShort) return { direction: "short", atr: atrVal };
    return { direction: "none", atr: atrVal };
  }

  shouldExit(candle: Candle, side: "long" | "short"): boolean {
    if (this.lastBBMid === 0) return false;
    if (side === "long" && candle.close >= this.lastBBMid) return true;
    if (side === "short" && candle.close <= this.lastBBMid) return true;
    return false;
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// RSI(2) Mean Reversion with indicator exit
// Entry: RSI(2) extreme. Exit: RSI crosses back to neutral.
// ══════════════════════════════════════════════════════════════════════════════

class RSI2ExitStrategy implements BacktestStrategy {
  private rsiGainRMA!: WildersRMA;
  private rsiLossRMA!: WildersRMA;
  private atrRMA!: WildersRMA;
  private ema!: EMA | null;
  private prevCloseRsi = 0;
  private rsiInit = false;
  private prevCloseAtr = 0;
  private lastRsi = 50;

  constructor(
    private readonly rsiPeriod: number = 2,
    private readonly rsiBuyThreshold: number = 10,
    private readonly rsiSellThreshold: number = 90,
    private readonly rsiExitLong: number = 60,
    private readonly rsiExitShort: number = 40,
    private readonly emaPeriod: number = 0, // 0 = no trend filter
  ) { this.reset(); }

  reset(): void {
    this.rsiGainRMA = new WildersRMA(this.rsiPeriod);
    this.rsiLossRMA = new WildersRMA(this.rsiPeriod);
    this.atrRMA = new WildersRMA(14);
    this.ema = this.emaPeriod > 0 ? new EMA(this.emaPeriod) : null;
    this.prevCloseRsi = 0;
    this.rsiInit = false;
    this.prevCloseAtr = 0;
    this.lastRsi = 50;
  }

  onCandle(candle: Candle): EntrySignal {
    const close = candle.close;
    const tr = calcTR(candle, this.prevCloseAtr);
    this.prevCloseAtr = close;
    const atrVal = this.atrRMA.update(tr);
    const emaVal = this.ema?.update(close) ?? null;

    let rsi: number | null = null;
    if (!this.rsiInit) { this.prevCloseRsi = close; this.rsiInit = true; }
    else {
      const ch = close - this.prevCloseRsi;
      this.prevCloseRsi = close;
      const ag = this.rsiGainRMA.update(Math.max(ch, 0));
      const al = this.rsiLossRMA.update(Math.max(-ch, 0));
      if (ag !== null && al !== null) rsi = al === 0 ? 100 : 100 - (100 / (1 + ag / al));
    }

    if (atrVal === null || rsi === null) return { direction: "none", atr: atrVal ?? 0 };
    if (this.ema && emaVal === null) return { direction: "none", atr: atrVal };
    this.lastRsi = rsi;

    const trendLong = emaVal !== null ? close > emaVal : true;
    const trendShort = emaVal !== null ? close < emaVal : true;

    if (rsi < this.rsiBuyThreshold && trendLong) return { direction: "long", atr: atrVal };
    if (rsi > this.rsiSellThreshold && trendShort) return { direction: "short", atr: atrVal };
    return { direction: "none", atr: atrVal };
  }

  shouldExit(candle: Candle, side: "long" | "short"): boolean {
    if (side === "long" && this.lastRsi >= this.rsiExitLong) return true;
    if (side === "short" && this.lastRsi <= this.rsiExitShort) return true;
    return false;
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// Consecutive Candle Reversal Strategy
// Entry: after N consecutive candles in one direction, enter opposite.
// Exit: first candle that closes in the entry direction (mean reversion).
// ══════════════════════════════════════════════════════════════════════════════

class ConsecRevStrategy implements BacktestStrategy {
  private atrRMA!: WildersRMA;
  private ema!: EMA | null;
  private prevCloseAtr = 0;
  private consecutiveBear = 0;
  private consecutiveBull = 0;
  private prevClose = 0;

  constructor(
    private readonly minConsec: number = 3,
    private readonly emaPeriod: number = 0, // 0 = no trend filter
  ) { this.reset(); }

  reset(): void {
    this.atrRMA = new WildersRMA(14);
    this.ema = this.emaPeriod > 0 ? new EMA(this.emaPeriod) : null;
    this.prevCloseAtr = 0;
    this.consecutiveBear = 0;
    this.consecutiveBull = 0;
    this.prevClose = 0;
  }

  onCandle(candle: Candle): EntrySignal {
    const close = candle.close;
    const tr = calcTR(candle, this.prevCloseAtr);
    this.prevCloseAtr = close;
    const atrVal = this.atrRMA.update(tr);
    const emaVal = this.ema?.update(close) ?? null;

    const bullish = candle.close > candle.open;
    if (bullish) { this.consecutiveBull++; this.consecutiveBear = 0; }
    else { this.consecutiveBear++; this.consecutiveBull = 0; }

    if (atrVal === null || this.prevClose === 0) {
      this.prevClose = close;
      return { direction: "none", atr: atrVal ?? 0 };
    }
    if (this.ema && emaVal === null) { this.prevClose = close; return { direction: "none", atr: atrVal }; }
    this.prevClose = close;

    const trendLong = emaVal !== null ? close > emaVal : true;
    const trendShort = emaVal !== null ? close < emaVal : true;

    if (this.consecutiveBear >= this.minConsec && trendLong) return { direction: "long", atr: atrVal };
    if (this.consecutiveBull >= this.minConsec && trendShort) return { direction: "short", atr: atrVal };
    return { direction: "none", atr: atrVal };
  }

  shouldExit(candle: Candle, side: "long" | "short"): boolean {
    if (side === "long" && candle.close > candle.open) return true;
    if (side === "short" && candle.close < candle.open) return true;
    return false;
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// Multi-Factor Mean Reversion: BB + RSI(14) for ultra-selective entries
// Entry: price at BB extreme AND RSI at extreme (double confirmation)
// Exit: RSI normalizes OR price reaches BB middle
// ══════════════════════════════════════════════════════════════════════════════

class MultiFacRevStrategy implements BacktestStrategy {
  private bb!: RollingBB;
  private rsiGainRMA!: WildersRMA;
  private rsiLossRMA!: WildersRMA;
  private atrRMA!: WildersRMA;
  private ema!: EMA | null;
  private prevCloseRsi = 0;
  private rsiInit = false;
  private prevCloseAtr = 0;
  private lastBBMid = 0;
  private lastRsi = 50;

  constructor(
    private readonly bbPeriod: number = 20,
    private readonly bbMult: number = 2.0,
    private readonly rsiPeriod: number = 14,
    private readonly rsiBuyThr: number = 30,
    private readonly rsiSellThr: number = 70,
    private readonly rsiExitLong: number = 50,
    private readonly rsiExitShort: number = 50,
    private readonly emaPeriod: number = 0,
  ) { this.reset(); }

  reset(): void {
    this.bb = new RollingBB(this.bbPeriod, this.bbMult);
    this.rsiGainRMA = new WildersRMA(this.rsiPeriod);
    this.rsiLossRMA = new WildersRMA(this.rsiPeriod);
    this.atrRMA = new WildersRMA(14);
    this.ema = this.emaPeriod > 0 ? new EMA(this.emaPeriod) : null;
    this.prevCloseRsi = 0;
    this.rsiInit = false;
    this.prevCloseAtr = 0;
    this.lastBBMid = 0;
    this.lastRsi = 50;
  }

  onCandle(candle: Candle): EntrySignal {
    const close = candle.close;
    const tr = calcTR(candle, this.prevCloseAtr);
    this.prevCloseAtr = close;
    const atrVal = this.atrRMA.update(tr);
    const emaVal = this.ema?.update(close) ?? null;
    const bbVal = this.bb.update(close);

    let rsi: number | null = null;
    if (!this.rsiInit) { this.prevCloseRsi = close; this.rsiInit = true; }
    else {
      const ch = close - this.prevCloseRsi;
      this.prevCloseRsi = close;
      const ag = this.rsiGainRMA.update(Math.max(ch, 0));
      const al = this.rsiLossRMA.update(Math.max(-ch, 0));
      if (ag !== null && al !== null) rsi = al === 0 ? 100 : 100 - (100 / (1 + ag / al));
    }

    if (atrVal === null || bbVal === null || rsi === null) return { direction: "none", atr: atrVal ?? 0 };
    if (this.ema && emaVal === null) return { direction: "none", atr: atrVal };
    this.lastBBMid = bbVal.sma;
    this.lastRsi = rsi;

    const trendLong = emaVal !== null ? close > emaVal : true;
    const trendShort = emaVal !== null ? close < emaVal : true;

    // Double confirmation: BB extreme + RSI extreme
    if (close <= bbVal.lower && rsi < this.rsiBuyThr && trendLong) return { direction: "long", atr: atrVal };
    if (close >= bbVal.upper && rsi > this.rsiSellThr && trendShort) return { direction: "short", atr: atrVal };
    return { direction: "none", atr: atrVal };
  }

  shouldExit(candle: Candle, side: "long" | "short"): boolean {
    // Exit when RSI normalizes OR price reaches BB middle
    if (side === "long") {
      return this.lastRsi >= this.rsiExitLong || candle.close >= this.lastBBMid;
    }
    return this.lastRsi <= this.rsiExitShort || candle.close <= this.lastBBMid;
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// Fade Large Move: counter-trade overextended single-candle moves
// Entry: candle body > N × ATR (overextended)
// Exit: next candle reversal or time
// ══════════════════════════════════════════════════════════════════════════════

class FadeMoveStrategy implements BacktestStrategy {
  private atrRMA!: WildersRMA;
  private prevCloseAtr = 0;
  private lastAtr = 0;
  private barsInPosition = 0;

  constructor(
    private readonly bodyAtrMult: number = 1.5, // candle body must be > N × ATR
    private readonly holdBars: number = 2,       // exit after N bars
  ) { this.reset(); }

  reset(): void {
    this.atrRMA = new WildersRMA(14);
    this.prevCloseAtr = 0;
    this.lastAtr = 0;
    this.barsInPosition = 0;
  }

  onCandle(candle: Candle): EntrySignal {
    const tr = calcTR(candle, this.prevCloseAtr);
    this.prevCloseAtr = candle.close;
    const atrVal = this.atrRMA.update(tr);
    if (atrVal === null) return { direction: "none", atr: 0 };
    this.lastAtr = atrVal;

    const body = Math.abs(candle.close - candle.open);
    if (body < this.bodyAtrMult * atrVal) return { direction: "none", atr: atrVal };

    // Fade the large move: big red candle → long, big green candle → short
    if (candle.close < candle.open) return { direction: "long", atr: atrVal };
    return { direction: "short", atr: atrVal };
  }

  shouldExit(candle: Candle, side: "long" | "short", _entry: number, barsHeld: number): boolean {
    return barsHeld >= this.holdBars;
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// Parameterized SQZ Strategy (for sweep testing)
// ══════════════════════════════════════════════════════════════════════════════

class SQZParamStrategy implements BacktestStrategy {
  private bb!: RollingBB;
  private kcEma!: EMA;
  private kcAtr!: WildersRMA;
  private macdFast!: EMA;
  private macdSlow!: EMA;
  private macdSignal!: EMA;
  private ema200!: EMA | null;
  private rsiGainRMA!: WildersRMA;
  private rsiLossRMA!: WildersRMA;
  private atrSL!: WildersRMA;
  private volSMA!: RollingSMA;

  private prevCloseAtr = 0;
  private prevCloseKc = 0;
  private prevCloseRsi = 0;
  private rsiInit = false;
  private prevSqzOn = false;
  private sqzConsecutive = 0;
  private lastSqzLength = 0;

  constructor(
    private readonly minSqzBars: number = 6,
    private readonly useEma200: boolean = true,
    private readonly useVolFilter: boolean = true,
    private readonly minVolRatio: number = 1.2,
    private readonly rsiMin: number = 30,
    private readonly rsiMax: number = 70,
    private readonly kcMult: number = 1.5,
  ) { this.reset(); }

  reset(): void {
    this.bb = new RollingBB(20, 2.0);
    this.kcEma = new EMA(20);
    this.kcAtr = new WildersRMA(10);
    this.macdFast = new EMA(12);
    this.macdSlow = new EMA(26);
    this.macdSignal = new EMA(9);
    this.ema200 = this.useEma200 ? new EMA(200) : null;
    this.rsiGainRMA = new WildersRMA(14);
    this.rsiLossRMA = new WildersRMA(14);
    this.atrSL = new WildersRMA(14);
    this.volSMA = new RollingSMA(20);
    this.prevCloseAtr = 0; this.prevCloseKc = 0;
    this.prevCloseRsi = 0; this.rsiInit = false;
    this.prevSqzOn = false; this.sqzConsecutive = 0;
    this.lastSqzLength = 0;
  }

  onCandle(candle: Candle): EntrySignal {
    const close = candle.close;
    const trSL = calcTR(candle, this.prevCloseAtr);
    this.prevCloseAtr = close;
    const atrSlVal = this.atrSL.update(trSL);
    const trKC = calcTR(candle, this.prevCloseKc);
    this.prevCloseKc = close;
    const kcAtrVal = this.kcAtr.update(trKC);
    const bbVal = this.bb.update(close);
    const kcMid = this.kcEma.update(close);
    const mf = this.macdFast.update(close);
    const ms = this.macdSlow.update(close);
    let macdHist: number | null = null;
    if (mf !== null && ms !== null) {
      const line = mf - ms;
      const sig = this.macdSignal.update(line);
      if (sig !== null) macdHist = line - sig;
    }
    const e200 = this.ema200?.update(close) ?? null;
    let rsi: number | null = null;
    if (!this.rsiInit) { this.prevCloseRsi = close; this.rsiInit = true; }
    else {
      const ch = close - this.prevCloseRsi; this.prevCloseRsi = close;
      const ag = this.rsiGainRMA.update(Math.max(ch, 0));
      const al = this.rsiLossRMA.update(Math.max(-ch, 0));
      if (ag !== null && al !== null) rsi = al === 0 ? 100 : 100 - (100 / (1 + ag / al));
    }
    const volSmaVal = this.volSMA.update(candle.volume);
    if (bbVal === null || kcMid === null || kcAtrVal === null || macdHist === null || rsi === null || atrSlVal === null || volSmaVal === null) {
      return { direction: "none", atr: 0 };
    }
    if (this.useEma200 && e200 === null) return { direction: "none", atr: atrSlVal };

    const kcUpper = kcMid + this.kcMult * kcAtrVal;
    const kcLower = kcMid - this.kcMult * kcAtrVal;
    const sqzOn = bbVal.lower > kcLower && bbVal.upper < kcUpper;
    if (sqzOn) { this.sqzConsecutive++; this.lastSqzLength = this.sqzConsecutive; }
    else { this.sqzConsecutive = 0; }
    const fired = this.prevSqzOn && !sqzOn;
    const prevLen = fired ? this.lastSqzLength : 0;
    this.prevSqzOn = sqzOn;

    if (!fired || prevLen < this.minSqzBars) return { direction: "none", atr: atrSlVal };
    // Volume filter
    if (this.useVolFilter) {
      const volRatio = volSmaVal > 0 ? candle.volume / volSmaVal : 0;
      if (volRatio < this.minVolRatio) return { direction: "none", atr: atrSlVal };
    }
    // RSI filter
    if (rsi < this.rsiMin || rsi > this.rsiMax) return { direction: "none", atr: atrSlVal };
    // Direction
    const trendLong = e200 !== null ? close > e200 : true;
    const trendShort = e200 !== null ? close < e200 : true;
    if (macdHist > 0 && trendLong) return { direction: "long", atr: atrSlVal };
    if (macdHist < 0 && trendShort) return { direction: "short", atr: atrSlVal };
    return { direction: "none", atr: atrSlVal };
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// Inside Bar Breakout Strategy
// Entry: breakout beyond parent bar after inside bar pattern
// ══════════════════════════════════════════════════════════════════════════════

class InsideBarStrategy implements BacktestStrategy {
  private atrRMA!: WildersRMA;
  private ema!: EMA | null;
  private prevCloseAtr = 0;
  private prevHigh = 0;
  private prevLow = 0;
  private prevPrevHigh = 0;
  private prevPrevLow = 0;
  private wasInsideBar = false;
  private parentHigh = 0;
  private parentLow = 0;

  constructor(
    private readonly emaPeriod: number = 50,
  ) { this.reset(); }

  reset(): void {
    this.atrRMA = new WildersRMA(14);
    this.ema = this.emaPeriod > 0 ? new EMA(this.emaPeriod) : null;
    this.prevCloseAtr = 0;
    this.prevHigh = 0; this.prevLow = 0;
    this.prevPrevHigh = 0; this.prevPrevLow = 0;
    this.wasInsideBar = false;
    this.parentHigh = 0; this.parentLow = 0;
  }

  onCandle(candle: Candle): EntrySignal {
    const tr = calcTR(candle, this.prevCloseAtr);
    this.prevCloseAtr = candle.close;
    const atrVal = this.atrRMA.update(tr);
    const emaVal = this.ema?.update(candle.close) ?? null;

    if (this.prevHigh === 0) {
      this.prevHigh = candle.high; this.prevLow = candle.low;
      return { direction: "none", atr: atrVal ?? 0 };
    }

    // Check if PREVIOUS bar was inside bar
    const isInsideBar = candle.high < this.prevHigh && candle.low > this.prevLow;

    // If we had an inside bar, check for breakout on this bar
    if (this.wasInsideBar && atrVal !== null) {
      if (this.ema && emaVal === null) { /* skip */ }
      else {
        const trendLong = emaVal !== null ? candle.close > emaVal : true;
        const trendShort = emaVal !== null ? candle.close < emaVal : true;

        if (candle.close > this.parentHigh && trendLong) {
          this.wasInsideBar = false;
          this.prevPrevHigh = this.prevHigh; this.prevPrevLow = this.prevLow;
          this.prevHigh = candle.high; this.prevLow = candle.low;
          return { direction: "long", atr: atrVal };
        }
        if (candle.close < this.parentLow && trendShort) {
          this.wasInsideBar = false;
          this.prevPrevHigh = this.prevHigh; this.prevPrevLow = this.prevLow;
          this.prevHigh = candle.high; this.prevLow = candle.low;
          return { direction: "short", atr: atrVal };
        }
      }
    }

    // Track inside bar state
    if (isInsideBar) {
      this.wasInsideBar = true;
      this.parentHigh = this.prevHigh;
      this.parentLow = this.prevLow;
    } else {
      this.wasInsideBar = false;
    }

    this.prevPrevHigh = this.prevHigh; this.prevPrevLow = this.prevLow;
    this.prevHigh = candle.high; this.prevLow = candle.low;
    return { direction: "none", atr: atrVal ?? 0 };
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// Engulfing Pattern Strategy
// Entry: bullish/bearish engulfing candle with trend filter
// ══════════════════════════════════════════════════════════════════════════════

class EngulfingStrategy implements BacktestStrategy {
  private atrRMA!: WildersRMA;
  private ema!: EMA | null;
  private prevCloseAtr = 0;
  private prevOpen = 0;
  private prevClose = 0;
  private prevHigh = 0;
  private prevLow = 0;
  private init = false;

  constructor(
    private readonly emaPeriod: number = 50,
  ) { this.reset(); }

  reset(): void {
    this.atrRMA = new WildersRMA(14);
    this.ema = this.emaPeriod > 0 ? new EMA(this.emaPeriod) : null;
    this.prevCloseAtr = 0;
    this.prevOpen = 0; this.prevClose = 0;
    this.prevHigh = 0; this.prevLow = 0;
    this.init = false;
  }

  onCandle(candle: Candle): EntrySignal {
    const tr = calcTR(candle, this.prevCloseAtr);
    this.prevCloseAtr = candle.close;
    const atrVal = this.atrRMA.update(tr);
    const emaVal = this.ema?.update(candle.close) ?? null;

    if (!this.init) {
      this.init = true;
      this.prevOpen = candle.open; this.prevClose = candle.close;
      this.prevHigh = candle.high; this.prevLow = candle.low;
      return { direction: "none", atr: atrVal ?? 0 };
    }
    if (atrVal === null) {
      this.prevOpen = candle.open; this.prevClose = candle.close;
      this.prevHigh = candle.high; this.prevLow = candle.low;
      return { direction: "none", atr: 0 };
    }
    if (this.ema && emaVal === null) {
      this.prevOpen = candle.open; this.prevClose = candle.close;
      this.prevHigh = candle.high; this.prevLow = candle.low;
      return { direction: "none", atr: atrVal };
    }

    const prevBearish = this.prevClose < this.prevOpen;
    const prevBullish = this.prevClose > this.prevOpen;
    const curBullish = candle.close > candle.open;
    const curBearish = candle.close < candle.open;

    const bullEngulf = prevBearish && curBullish &&
      candle.open <= this.prevClose && candle.close >= this.prevOpen;
    const bearEngulf = prevBullish && curBearish &&
      candle.open >= this.prevClose && candle.close <= this.prevOpen;

    const trendLong = emaVal !== null ? candle.close > emaVal : true;
    const trendShort = emaVal !== null ? candle.close < emaVal : true;

    this.prevOpen = candle.open; this.prevClose = candle.close;
    this.prevHigh = candle.high; this.prevLow = candle.low;

    if (bullEngulf && trendLong) return { direction: "long", atr: atrVal };
    if (bearEngulf && trendShort) return { direction: "short", atr: atrVal };
    return { direction: "none", atr: atrVal };
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// MACD Zero-Cross Momentum Strategy
// Entry: MACD histogram crosses zero with EMA trend filter
// ══════════════════════════════════════════════════════════════════════════════

class MACDCrossStrategy implements BacktestStrategy {
  private macdFast!: EMA;
  private macdSlow!: EMA;
  private macdSignal!: EMA;
  private atrRMA!: WildersRMA;
  private ema!: EMA | null;
  private prevCloseAtr = 0;
  private prevMacdHist = 0;

  constructor(
    private readonly emaPeriod: number = 50,
  ) { this.reset(); }

  reset(): void {
    this.macdFast = new EMA(12);
    this.macdSlow = new EMA(26);
    this.macdSignal = new EMA(9);
    this.atrRMA = new WildersRMA(14);
    this.ema = this.emaPeriod > 0 ? new EMA(this.emaPeriod) : null;
    this.prevCloseAtr = 0;
    this.prevMacdHist = 0;
  }

  onCandle(candle: Candle): EntrySignal {
    const close = candle.close;
    const tr = calcTR(candle, this.prevCloseAtr);
    this.prevCloseAtr = close;
    const atrVal = this.atrRMA.update(tr);
    const emaVal = this.ema?.update(close) ?? null;
    const mf = this.macdFast.update(close);
    const ms = this.macdSlow.update(close);
    let macdHist: number | null = null;
    if (mf !== null && ms !== null) {
      const line = mf - ms;
      const sig = this.macdSignal.update(line);
      if (sig !== null) macdHist = line - sig;
    }

    if (atrVal === null || macdHist === null) return { direction: "none", atr: atrVal ?? 0 };
    if (this.ema && emaVal === null) { this.prevMacdHist = macdHist; return { direction: "none", atr: atrVal }; }

    const bullCross = this.prevMacdHist <= 0 && macdHist > 0;
    const bearCross = this.prevMacdHist >= 0 && macdHist < 0;
    this.prevMacdHist = macdHist;

    const trendLong = emaVal !== null ? close > emaVal : true;
    const trendShort = emaVal !== null ? close < emaVal : true;

    if (bullCross && trendLong) return { direction: "long", atr: atrVal };
    if (bearCross && trendShort) return { direction: "short", atr: atrVal };
    return { direction: "none", atr: atrVal };
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// KC Breakout Strategy (Keltner Channel breakout)
// Entry: close breaks above/below Keltner Channel
// ══════════════════════════════════════════════════════════════════════════════

class KCBreakoutStrategy implements BacktestStrategy {
  private kcEma!: EMA;
  private kcAtr!: WildersRMA;
  private atrSL!: WildersRMA;
  private ema!: EMA | null;
  private prevCloseKc = 0;
  private prevCloseAtr = 0;

  constructor(
    private readonly kcPeriod: number = 20,
    private readonly kcAtrPeriod: number = 10,
    private readonly kcMult: number = 2.0,
    private readonly emaPeriod: number = 50,
  ) { this.reset(); }

  reset(): void {
    this.kcEma = new EMA(this.kcPeriod);
    this.kcAtr = new WildersRMA(this.kcAtrPeriod);
    this.atrSL = new WildersRMA(14);
    this.ema = this.emaPeriod > 0 ? new EMA(this.emaPeriod) : null;
    this.prevCloseKc = 0;
    this.prevCloseAtr = 0;
  }

  onCandle(candle: Candle): EntrySignal {
    const close = candle.close;
    const trKC = calcTR(candle, this.prevCloseKc);
    this.prevCloseKc = close;
    const kcAtrVal = this.kcAtr.update(trKC);
    const kcMid = this.kcEma.update(close);
    const trSL = calcTR(candle, this.prevCloseAtr);
    this.prevCloseAtr = close;
    const atrSlVal = this.atrSL.update(trSL);
    const emaVal = this.ema?.update(close) ?? null;

    if (kcMid === null || kcAtrVal === null || atrSlVal === null) return { direction: "none", atr: atrSlVal ?? 0 };
    if (this.ema && emaVal === null) return { direction: "none", atr: atrSlVal };

    const kcUpper = kcMid + this.kcMult * kcAtrVal;
    const kcLower = kcMid - this.kcMult * kcAtrVal;

    const trendLong = emaVal !== null ? close > emaVal : true;
    const trendShort = emaVal !== null ? close < emaVal : true;

    if (close > kcUpper && trendLong) return { direction: "long", atr: atrSlVal };
    if (close < kcLower && trendShort) return { direction: "short", atr: atrSlVal };
    return { direction: "none", atr: atrSlVal };
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// Main runner
// ══════════════════════════════════════════════════════════════════════════════

const BASE_CONFIG: BacktestConfig = {
  initialCapital: 100,
  takerFeePct: 0.00035,
  // Note: 50 BPS is the IOC buffer tolerance (aggressive limit price),
  // NOT the expected fill slippage. Real slippage on BTC/ETH Binance Futures
  // for small sizes is 2-5 BPS. Use 5 BPS for conservative backtest.
  slippageBps: 5,
  riskPct: 0.01,
  slAtrMult: 1.5,
  rrRatio: 3.0,
  breakEvenAtR: 1.0,
  timeExitBars: 45,
  maxLeverage: 50,
};

async function main(): Promise<void> {
  const args = process.argv.slice(2);
  if (args.length < 2) {
    console.log("Usage: tsx src/backtest/runner.ts <bot> <symbol> [days] [timeframe]");
    console.log("  bot:       utbot | sqzbot | sqzopt | meanrev | newbot | all");
    console.log("  symbol:    BTC | ETH | SOL");
    console.log("  days:      number of days (default: 30)");
    console.log("  timeframe: 1m | 5m | 15m (default: 1m)");
    process.exit(1);
  }

  const [bot, symbol, daysStr, tfStr] = args;
  const days = parseInt(daysStr ?? "30", 10);
  const tfMinutes = tfStr ? parseInt(tfStr.replace("m", ""), 10) : 1;

  console.log(`\nBacktest: ${bot.toUpperCase()} on ${symbol.toUpperCase()} for ${days} days (${tfMinutes}m candles)`);
  console.log(`Config: ${(BASE_CONFIG.riskPct * 100).toFixed(1)}% risk, 1:${BASE_CONFIG.rrRatio} RR, SL=${BASE_CONFIG.slAtrMult}xATR, BE at ${BASE_CONFIG.breakEvenAtR}R, time=${BASE_CONFIG.timeExitBars}bars\n`);

  const candles1m = await fetchCandles(symbol, days);
  const candles = aggregateCandles(candles1m, tfMinutes);
  console.log(`Total candles: ${candles.length} (${tfMinutes}m from ${candles1m.length} 1m candles)\n`);

  const runBot = (name: string, strategy: BacktestStrategy, cfgOverrides?: Partial<BacktestConfig>) => {
    const cfg = { ...BASE_CONFIG, ...cfgOverrides };
    const result = runBacktest(candles, strategy, cfg);
    printResult(name, result);
    return result;
  };

  const results: { name: string; result: ReturnType<typeof runBacktest> }[] = [];

  if (bot === "utbot" || bot === "all") {
    const r = runBot("Donchian Breakout (50-period + ADX20)", new UTBotStrategy(50, 20), { timeExitBars: 60 });
    results.push({ name: "Donchian", result: r });
  }

  if (bot === "sqzbot" || bot === "all") {
    const r = runBot("SQZ Bot (Squeeze + MACD + 1:3 RR)", new SQZBotStrategy());
    results.push({ name: "SQZ Bot", result: r });
  }

  if (bot === "meanrev" || bot === "all") {
    // Variant A: RSI(2)<5 extreme, 1:1 RR, EMA50 trend filter
    const rA = runBot("MR-A RSI2<5 1:1", new MeanRevStrategy(2, 5, 95, 50), {
      rrRatio: 1.0, slAtrMult: 1.5, breakEvenAtR: 99, timeExitBars: 20,
    });
    results.push({ name: "MR-A 1:1", result: rA });

    // Variant B: RSI(2)<10, small TP (0.5x SL) = 1:0.5 RR, wide SL
    const rB = runBot("MR-B RSI2<10 1:0.5", new MeanRevStrategy(2, 10, 90, 50), {
      rrRatio: 0.5, slAtrMult: 2.0, breakEvenAtR: 99, timeExitBars: 15,
    });
    results.push({ name: "MR-B 1:.5", result: rB });

    // Variant C: RSI(2)<10, tiny TP (0.33x SL) = 1:0.33 RR, wide SL
    const rC = runBot("MR-C RSI2<10 1:0.33", new MeanRevStrategy(2, 10, 90, 50), {
      rrRatio: 0.33, slAtrMult: 2.0, breakEvenAtR: 99, timeExitBars: 15,
    });
    results.push({ name: "MR-C 1:.3", result: rC });

    // Variant D: RSI(2)<5 no trend filter, 1:0.5 RR
    const rD = runBot("MR-D RSI2<5 noTrend", new MeanRevStrategy(2, 5, 95, 3), {
      rrRatio: 0.5, slAtrMult: 2.0, breakEvenAtR: 99, timeExitBars: 15,
    });
    results.push({ name: "MR-D noT", result: rD });

    // Variant E: RSI(2)<5, 1:1 RR, wider SL 2.5x ATR (more room)
    const rE = runBot("MR-E RSI2<5 wideSL", new MeanRevStrategy(2, 5, 95, 50), {
      rrRatio: 1.0, slAtrMult: 2.5, breakEvenAtR: 99, timeExitBars: 30,
    });
    results.push({ name: "MR-E wide", result: rE });
  }

  if (bot === "newbot" || bot === "all") {
    const s = new BBRevStrategy(20, 2.0, 50);
    // Optimize around BB EMA50 hybrid (best so far: 60.7% WR)
    // Vary SL width
    results.push({ name: "BB50 s1.5", result: runBot("BB e50 SL1.5 TP.5", s, { rrRatio: 0.5, breakEvenAtR: 99, timeExitBars: 30, slAtrMult: 1.5 }) });
    results.push({ name: "BB50 s2.0", result: runBot("BB e50 SL2.0 TP.5", new BBRevStrategy(20, 2.0, 50), { rrRatio: 0.5, breakEvenAtR: 99, timeExitBars: 30, slAtrMult: 2.0 }) });
    results.push({ name: "BB50 s2.5", result: runBot("BB e50 SL2.5 TP.5", new BBRevStrategy(20, 2.0, 50), { rrRatio: 0.5, breakEvenAtR: 99, timeExitBars: 30, slAtrMult: 2.5 }) });
    results.push({ name: "BB50 s3.0", result: runBot("BB e50 SL3.0 TP.5", new BBRevStrategy(20, 2.0, 50), { rrRatio: 0.5, breakEvenAtR: 99, timeExitBars: 30, slAtrMult: 3.0 }) });
    // Vary TP ratio
    results.push({ name: "BB50 tp0", result: runBot("BB e50 SL2 sigOnly", new BBRevStrategy(20, 2.0, 50), { rrRatio: 0, breakEvenAtR: 99, timeExitBars: 30, slAtrMult: 2.0 }) });
    results.push({ name: "BB50 tp.3", result: runBot("BB e50 SL2 TP.3", new BBRevStrategy(20, 2.0, 50), { rrRatio: 0.3, breakEvenAtR: 99, timeExitBars: 30, slAtrMult: 2.0 }) });
    results.push({ name: "BB50 tp.75", result: runBot("BB e50 SL2 TP.75", new BBRevStrategy(20, 2.0, 50), { rrRatio: 0.75, breakEvenAtR: 99, timeExitBars: 30, slAtrMult: 2.0 }) });
    results.push({ name: "BB50 tp1.0", result: runBot("BB e50 SL2 TP1.0", new BBRevStrategy(20, 2.0, 50), { rrRatio: 1.0, breakEvenAtR: 99, timeExitBars: 30, slAtrMult: 2.0 }) });
    // Aggressive BE (0.1R and 0.2R)
    results.push({ name: "BB50 be.1", result: runBot("BB e50 SL2 BE.1", new BBRevStrategy(20, 2.0, 50), { rrRatio: 0.5, breakEvenAtR: 0.1, timeExitBars: 30, slAtrMult: 2.0 }) });
    results.push({ name: "BB50 be.2", result: runBot("BB e50 SL2 BE.2", new BBRevStrategy(20, 2.0, 50), { rrRatio: 0.5, breakEvenAtR: 0.2, timeExitBars: 30, slAtrMult: 2.0 }) });
    // Time exit variation
    results.push({ name: "BB50 t15", result: runBot("BB e50 SL2 T15", new BBRevStrategy(20, 2.0, 50), { rrRatio: 0.5, breakEvenAtR: 99, timeExitBars: 15, slAtrMult: 2.0 }) });
    results.push({ name: "BB50 t45", result: runBot("BB e50 SL2 T45", new BBRevStrategy(20, 2.0, 50), { rrRatio: 0.5, breakEvenAtR: 99, timeExitBars: 45, slAtrMult: 2.0 }) });
    // EMA200 trend filter
    results.push({ name: "BB200 s2", result: runBot("BB e200 SL2 TP.5", new BBRevStrategy(20, 2.0, 200), { rrRatio: 0.5, breakEvenAtR: 99, timeExitBars: 30, slAtrMult: 2.0 }) });
    // No trend, wider SL + aggressive BE combo
    results.push({ name: "BBnoT+BE", result: runBot("BB noT SL3 BE.15", new BBRevStrategy(20, 2.0, 0), { rrRatio: 0.5, breakEvenAtR: 0.15, timeExitBars: 30, slAtrMult: 3.0 }) });
    // Multi-factor: BB + RSI14 < 30
    results.push({ name: "MF 14<30", result: runBot("MF RSI14<30", new MultiFacRevStrategy(20, 2.0, 14, 30, 70, 50, 50, 0), { rrRatio: 0.5, breakEvenAtR: 99, timeExitBars: 30, slAtrMult: 2.0 }) });
    results.push({ name: "MF e50", result: runBot("MF RSI14 e50", new MultiFacRevStrategy(20, 2.0, 14, 30, 70, 50, 50, 50), { rrRatio: 0.5, breakEvenAtR: 99, timeExitBars: 30, slAtrMult: 2.0 }) });
    // Fade large moves
    results.push({ name: "Fade 1.5x", result: runBot("Fade 1.5xATR", new FadeMoveStrategy(1.5, 2), { rrRatio: 0.5, breakEvenAtR: 99, slAtrMult: 2.5, timeExitBars: 10 }) });
    results.push({ name: "Fade 2.0x", result: runBot("Fade 2.0xATR", new FadeMoveStrategy(2.0, 2), { rrRatio: 0.5, breakEvenAtR: 99, slAtrMult: 2.5, timeExitBars: 10 }) });
  }

  if (bot === "replace" || bot === "all") {
    // ── Supertrend variants ──────────────────────────────────────────────
    // Default: ATR(10) x3, ADX>20, MACD confirm
    results.push({ name: "ST default", result: runBot("Supertrend default", new SupertrendStrategy(10, 3.0, 20, true)) });
    // Higher mult = fewer signals
    results.push({ name: "ST mult4", result: runBot("Supertrend mult4", new SupertrendStrategy(10, 4.0, 20, true)) });
    // No MACD filter
    results.push({ name: "ST noMACD", result: runBot("Supertrend noMACD", new SupertrendStrategy(10, 3.0, 20, false)) });
    // Stricter ADX
    results.push({ name: "ST adx30", result: runBot("Supertrend ADX30", new SupertrendStrategy(10, 3.0, 30, true)) });
    // Longer ATR period
    results.push({ name: "ST atr14", result: runBot("Supertrend ATR14", new SupertrendStrategy(14, 3.0, 20, true)) });
    // No ADX, just MACD
    results.push({ name: "ST noADX", result: runBot("Supertrend noADX", new SupertrendStrategy(10, 3.0, 0, true)) });

    // ── Donchian Breakout variants ───────────────────────────────────────
    results.push({ name: "Donch 20", result: runBot("Donchian 20 ADX20", new DonchianBreakoutStrategy(20, 20)) });
    results.push({ name: "Donch 30", result: runBot("Donchian 30 ADX20", new DonchianBreakoutStrategy(30, 20)) });
    results.push({ name: "Donch 50", result: runBot("Donchian 50 ADX20", new DonchianBreakoutStrategy(50, 20)) });
    results.push({ name: "Donch noADX", result: runBot("Donchian 20 noADX", new DonchianBreakoutStrategy(20, 0)) });
    results.push({ name: "Donch 20a30", result: runBot("Donchian 20 ADX30", new DonchianBreakoutStrategy(20, 30)) });

    // ── EMA Crossover + ADX + MACD ───────────────────────────────────────
    results.push({ name: "EMA 9/21", result: runBot("EMA 9/21 ADX25", new EMACrossADXStrategy(9, 21, 25)) });
    results.push({ name: "EMA 8/21", result: runBot("EMA 8/21 ADX25", new EMACrossADXStrategy(8, 21, 25)) });
    results.push({ name: "EMA 9/21a20", result: runBot("EMA 9/21 ADX20", new EMACrossADXStrategy(9, 21, 20)) });
    results.push({ name: "EMA 12/26", result: runBot("EMA 12/26 ADX25", new EMACrossADXStrategy(12, 26, 25)) });
    results.push({ name: "EMA noADX", result: runBot("EMA 9/21 noADX", new EMACrossADXStrategy(9, 21, 0)) });

    // ── RSI Momentum ─────────────────────────────────────────────────────
    results.push({ name: "RSImom 55", result: runBot("RSI mom 55/45 e50", new RSIMomentumStrategy(14, 55, 45, 50)) });
    results.push({ name: "RSImom 60", result: runBot("RSI mom 60/40 e50", new RSIMomentumStrategy(14, 60, 40, 50)) });
    results.push({ name: "RSImom e200", result: runBot("RSI mom 55/45 e200", new RSIMomentumStrategy(14, 55, 45, 200)) });

    // ── UT Bot baseline for comparison ───────────────────────────────────
    results.push({ name: "UTBot base", result: runBot("UT Bot baseline", new UTBotStrategy(2, 14)) });
  }

  if (bot === "v2") {
    // Focused test: best candidates with different RR ratios, trailing stops, risk levels

    // ── Turtle Strategy (trailing stop exit, NO fixed TP) ──────────────
    // rrRatio=0 disables fixed TP, shouldExit handles trailing stop
    results.push({ name: "Turt 20/10", result: runBot("Turtle 20/10 trail", new TurtleStrategy(20, 10), { rrRatio: 0, breakEvenAtR: 99, timeExitBars: 60 }) });
    results.push({ name: "Turt 50/20", result: runBot("Turtle 50/20 trail", new TurtleStrategy(50, 20), { rrRatio: 0, breakEvenAtR: 99, timeExitBars: 60 }) });
    results.push({ name: "Turt 20 rr2", result: runBot("Turtle 20 RR2", new TurtleStrategy(20, 10), { rrRatio: 2.0, breakEvenAtR: 99, timeExitBars: 60 }) });
    results.push({ name: "Turt 20 rr1.5", result: runBot("Turtle 20 RR1.5", new TurtleStrategy(20, 10), { rrRatio: 1.5, breakEvenAtR: 99, timeExitBars: 60 }) });

    // ── Donchian 50 with different RR (was the best trend-follower) ────
    results.push({ name: "D50 rr3", result: runBot("Donch50 RR3", new DonchianBreakoutStrategy(50, 20), { rrRatio: 3.0, breakEvenAtR: 1.0, timeExitBars: 60 }) });
    results.push({ name: "D50 rr2", result: runBot("Donch50 RR2", new DonchianBreakoutStrategy(50, 20), { rrRatio: 2.0, breakEvenAtR: 1.0, timeExitBars: 60 }) });
    results.push({ name: "D50 rr1.5", result: runBot("Donch50 RR1.5", new DonchianBreakoutStrategy(50, 20), { rrRatio: 1.5, breakEvenAtR: 1.0, timeExitBars: 60 }) });
    results.push({ name: "D50 rr1", result: runBot("Donch50 RR1", new DonchianBreakoutStrategy(50, 20), { rrRatio: 1.0, breakEvenAtR: 1.0, timeExitBars: 60 }) });
    // Donch50 with wider SL
    results.push({ name: "D50 sl2.5", result: runBot("Donch50 SL2.5 RR2", new DonchianBreakoutStrategy(50, 20), { rrRatio: 2.0, slAtrMult: 2.5, breakEvenAtR: 1.0, timeExitBars: 60 }) });
    results.push({ name: "D50 sl3", result: runBot("Donch50 SL3 RR2", new DonchianBreakoutStrategy(50, 20), { rrRatio: 2.0, slAtrMult: 3.0, breakEvenAtR: 1.0, timeExitBars: 60 }) });

    // ── Pullback strategy variants ───────────────────────────────────────
    results.push({ name: "PB 20/50", result: runBot("Pullback 20/50 rsi40", new PullbackStrategy(20, 50, 40, 60, 20), { rrRatio: 2.0, breakEvenAtR: 1.0, timeExitBars: 30 }) });
    results.push({ name: "PB 20/50r35", result: runBot("Pullback 20/50 rsi35", new PullbackStrategy(20, 50, 35, 65, 20), { rrRatio: 2.0, breakEvenAtR: 1.0, timeExitBars: 30 }) });
    results.push({ name: "PB 10/50", result: runBot("Pullback 10/50 rsi40", new PullbackStrategy(10, 50, 40, 60, 20), { rrRatio: 2.0, breakEvenAtR: 1.0, timeExitBars: 30 }) });
    results.push({ name: "PB noADX", result: runBot("Pullback 20/50 noADX", new PullbackStrategy(20, 50, 40, 60, 0), { rrRatio: 2.0, breakEvenAtR: 1.0, timeExitBars: 30 }) });
    // Pullback with 1:3 RR
    results.push({ name: "PB rr3", result: runBot("Pullback 20/50 rr3", new PullbackStrategy(20, 50, 40, 60, 20), { rrRatio: 3.0, breakEvenAtR: 1.0, timeExitBars: 45 }) });

    // ── Higher risk (1.5% and 2%) on best strategies ─────────────────────
    results.push({ name: "D50 2%", result: runBot("Donch50 risk2%", new DonchianBreakoutStrategy(50, 20), { riskPct: 0.02, rrRatio: 2.0, breakEvenAtR: 1.0, timeExitBars: 60 }) });
    results.push({ name: "PB 2%", result: runBot("Pullback risk2%", new PullbackStrategy(20, 50, 40, 60, 20), { riskPct: 0.02, rrRatio: 2.0, breakEvenAtR: 1.0, timeExitBars: 30 }) });
    results.push({ name: "Turt 2%", result: runBot("Turtle risk2%", new TurtleStrategy(20, 10), { riskPct: 0.02, rrRatio: 0, breakEvenAtR: 99, timeExitBars: 60 }) });
  }

  if (bot === "sqzopt") {
    // ── SQZ variants with relaxed parameters ──────────────────────────
    // Original: minSqz=6, EMA200=on, vol=on, volRatio=1.2, RSI 30-70, KC=1.5
    results.push({ name: "SQZ base", result: runBot("SQZ original", new SQZParamStrategy(6, true, true, 1.2, 30, 70, 1.5)) });
    results.push({ name: "SQZ noEMA", result: runBot("SQZ no EMA200", new SQZParamStrategy(6, false, true, 1.2, 30, 70, 1.5)) });
    results.push({ name: "SQZ noVol", result: runBot("SQZ no volume", new SQZParamStrategy(6, true, false, 1.2, 30, 70, 1.5)) });
    results.push({ name: "SQZ bare", result: runBot("SQZ no EMA no vol", new SQZParamStrategy(6, false, false, 1.2, 30, 70, 1.5)) });
    results.push({ name: "SQZ sqz3", result: runBot("SQZ minSqz=3", new SQZParamStrategy(3, false, false, 1.2, 30, 70, 1.5)) });
    results.push({ name: "SQZ sqz2", result: runBot("SQZ minSqz=2", new SQZParamStrategy(2, false, false, 1.2, 30, 70, 1.5)) });
    results.push({ name: "SQZ rsi25", result: runBot("SQZ RSI 25-75", new SQZParamStrategy(3, false, false, 1.2, 25, 75, 1.5)) });
    results.push({ name: "SQZ rsiNo", result: runBot("SQZ RSI 0-100", new SQZParamStrategy(3, false, false, 1.2, 0, 100, 1.5)) });
    results.push({ name: "SQZ kc1.2", result: runBot("SQZ KC=1.2", new SQZParamStrategy(3, false, false, 1.2, 0, 100, 1.2)) });
    results.push({ name: "SQZ kc1.0", result: runBot("SQZ KC=1.0", new SQZParamStrategy(3, false, false, 1.2, 0, 100, 1.0)) });

    // ── SQZ variants with 1:2 RR ─────────────────────────────────────
    results.push({ name: "SQZ rr2", result: runBot("SQZ bare RR2", new SQZParamStrategy(3, false, false, 1.2, 0, 100, 1.5), { rrRatio: 2.0 }) });
    results.push({ name: "SQZ rr1.5", result: runBot("SQZ bare RR1.5", new SQZParamStrategy(3, false, false, 1.2, 0, 100, 1.5), { rrRatio: 1.5 }) });
    results.push({ name: "SQZ rr1", result: runBot("SQZ bare RR1.0", new SQZParamStrategy(3, false, false, 1.2, 0, 100, 1.5), { rrRatio: 1.0 }) });

    // ── Price action strategies ───────────────────────────────────────
    results.push({ name: "IB e50", result: runBot("InsideBar EMA50", new InsideBarStrategy(50), { rrRatio: 2.0, timeExitBars: 30 }) });
    results.push({ name: "IB noE", result: runBot("InsideBar noEMA", new InsideBarStrategy(0), { rrRatio: 2.0, timeExitBars: 30 }) });
    results.push({ name: "IB e50r3", result: runBot("InsideBar RR3", new InsideBarStrategy(50), { rrRatio: 3.0, timeExitBars: 30 }) });
    results.push({ name: "Eng e50", result: runBot("Engulfing EMA50", new EngulfingStrategy(50), { rrRatio: 2.0, timeExitBars: 30 }) });
    results.push({ name: "Eng noE", result: runBot("Engulfing noEMA", new EngulfingStrategy(0), { rrRatio: 2.0, timeExitBars: 30 }) });
    results.push({ name: "Eng e200", result: runBot("Engulfing EMA200", new EngulfingStrategy(200), { rrRatio: 2.0, timeExitBars: 30 }) });

    // ── MACD Zero-Cross ──────────────────────────────────────────────
    results.push({ name: "MACD e50", result: runBot("MACD cross EMA50", new MACDCrossStrategy(50), { rrRatio: 2.0, timeExitBars: 30 }) });
    results.push({ name: "MACD e200", result: runBot("MACD cross EMA200", new MACDCrossStrategy(200), { rrRatio: 2.0, timeExitBars: 30 }) });
    results.push({ name: "MACD noE", result: runBot("MACD cross noEMA", new MACDCrossStrategy(0), { rrRatio: 2.0, timeExitBars: 30 }) });

    // ── KC Breakout ──────────────────────────────────────────────────
    results.push({ name: "KC 2.0", result: runBot("KC brkout 2.0 e50", new KCBreakoutStrategy(20, 10, 2.0, 50), { rrRatio: 2.0, timeExitBars: 30 }) });
    results.push({ name: "KC 1.5", result: runBot("KC brkout 1.5 e50", new KCBreakoutStrategy(20, 10, 1.5, 50), { rrRatio: 2.0, timeExitBars: 30 }) });
    results.push({ name: "KC 2.5", result: runBot("KC brkout 2.5 e50", new KCBreakoutStrategy(20, 10, 2.5, 50), { rrRatio: 2.0, timeExitBars: 30 }) });
    results.push({ name: "KC noE", result: runBot("KC brkout 2.0 noEMA", new KCBreakoutStrategy(20, 10, 2.0, 0), { rrRatio: 2.0, timeExitBars: 30 }) });
  }

  if (results.length > 1) {
    console.log("\n" + "=".repeat(60));
    console.log("  COMPARISON");
    console.log("=".repeat(60));
    console.log("┌──────────────┬──────────┬──────────┬──────────┬──────────┬──────────┐");
    console.log("│ Bot          │ P&L %    │ WR %     │ Expect R │ Trades   │ Max DD % │");
    console.log("├──────────────┼──────────┼──────────┼──────────┼──────────┼──────────┤");
    for (const { name, result } of results) {
      const pnl = `${result.totalPnlPct >= 0 ? "+" : ""}${result.totalPnlPct.toFixed(1)}%`;
      const wr = `${result.winRate.toFixed(1)}%`;
      const exp = `${result.expectancyR >= 0 ? "+" : ""}${result.expectancyR.toFixed(3)}R`;
      const tr = `${result.numTrades}`;
      const dd = `${result.maxDrawdownPct.toFixed(1)}%`;
      console.log(`│ ${name.padEnd(12)} │ ${pnl.padEnd(8)} │ ${wr.padEnd(8)} │ ${exp.padEnd(8)} │ ${tr.padEnd(8)} │ ${dd.padEnd(8)} │`);
    }
    console.log("└──────────────┴──────────┴──────────┴──────────┴──────────┴──────────┘");
  }
}

main().catch((err) => {
  console.error("Backtest failed:", err);
  process.exit(1);
});
