/**
 * Backtesting engine with fixed SL/TP risk management.
 *
 * Key features:
 *   - Risk-based sizing: size = (balance × riskPct) / slDistance
 *   - Fixed SL/TP: SL = slAtrMult × ATR, TP = rrRatio × SL (1:3 RR)
 *   - Break-even: SL moves to entry+fees when profit = breakEvenAtR × slDistance
 *   - Intra-candle SL/TP: checks high/low within each bar
 *   - Time exit: forced close after N bars
 *   - No same-bar re-entry: after exit, wait for next bar to enter
 */

import type { Candle } from "./data.js";

export interface EntrySignal {
  direction: "long" | "short" | "none";
  atr: number;
}

export interface BacktestStrategy {
  onCandle(candle: Candle): EntrySignal;
  /** Optional indicator-based exit. Called each bar while in position. */
  shouldExit?(candle: Candle, side: "long" | "short", entryPrice: number, barsHeld: number): boolean;
  reset(): void;
}

export interface BacktestConfig {
  initialCapital: number;
  takerFeePct: number;
  slippageBps: number;
  riskPct: number;
  slAtrMult: number;
  rrRatio: number;
  breakEvenAtR: number;
  timeExitBars: number;
  maxLeverage: number;
}

interface Trade {
  entryTime: number;
  exitTime: number;
  side: "long" | "short";
  entryPrice: number;
  exitPrice: number;
  size: number;
  pnl: number;
  netPnl: number;
  fees: number;
  exitReason: "tp" | "sl" | "be" | "time" | "end" | "signal";
  barsHeld: number;
  riskAmount: number;
}

export interface BacktestResult {
  totalPnl: number;
  totalPnlPct: number;
  totalFees: number;
  numTrades: number;
  winRate: number;
  wins: number;
  losses: number;
  breakEvens: number;
  avgWinR: number;
  avgLossR: number;
  expectancyR: number;
  profitFactor: number;
  maxDrawdown: number;
  maxDrawdownPct: number;
  sharpeRatio: number;
  avgTradeDuration: number;
  finalCapital: number;
  trades: Trade[];
}

export function runBacktest(
  candles: Candle[],
  strategy: BacktestStrategy,
  config: BacktestConfig,
): BacktestResult {
  strategy.reset();

  const trades: Trade[] = [];
  let capital = config.initialCapital;
  let peakCapital = capital;
  let maxDrawdown = 0;

  let inPosition = false;
  let side: "long" | "short" = "long";
  let entryPrice = 0;
  let posSize = 0;
  let stopLoss = 0;
  let takeProfit = 0;
  let slDistance = 0;
  let entryTime = 0;
  let entryBar = 0;
  let breakEvenActive = false;
  let entryFee = 0;
  let riskAmount = 0;
  let barCount = 0;

  const slipFactor = config.slippageBps / 10_000;

  function fp(price: number, isBuy: boolean): number {
    return isBuy ? price * (1 + slipFactor) : price * (1 - slipFactor);
  }

  function closeTrade(exitPrice: number, reason: Trade["exitReason"], candle: Candle): void {
    const exitFill = fp(exitPrice, side === "short");
    const exitFee = posSize * exitFill * config.takerFeePct;
    const totalFees = entryFee + exitFee;
    const grossPnl = side === "long"
      ? (exitFill - entryPrice) * posSize
      : (entryPrice - exitFill) * posSize;
    const netPnl = grossPnl - totalFees;

    capital += netPnl;
    peakCapital = Math.max(peakCapital, capital);
    maxDrawdown = Math.max(maxDrawdown, peakCapital - capital);

    trades.push({
      entryTime, exitTime: candle.time, side, entryPrice,
      exitPrice: exitFill, size: posSize, pnl: grossPnl, netPnl,
      fees: totalFees, exitReason: reason,
      barsHeld: barCount - entryBar, riskAmount,
    });

    inPosition = false;
    posSize = 0;
  }

  for (const candle of candles) {
    barCount++;
    let exitedThisBar = false;

    // ── 1. Always feed candle to strategy (keeps indicators warm) ────────
    const signal = strategy.onCandle(candle);

    // ── 2. If in position: check exits on THIS candle's high/low ────────
    if (inPosition) {
      const barsHeld = barCount - entryBar;

      if (side === "long") {
        // SL check (pessimistic: check SL before TP)
        if (candle.low <= stopLoss) {
          closeTrade(stopLoss, breakEvenActive ? "be" : "sl", candle);
          exitedThisBar = true;
        }
        // TP check (skip if rrRatio=0 means no fixed TP)
        else if (config.rrRatio > 0 && candle.high >= takeProfit) {
          closeTrade(takeProfit, "tp", candle);
          exitedThisBar = true;
        }
        else {
          // Break-even activation
          if (!breakEvenActive && config.breakEvenAtR < 90 && candle.high >= entryPrice + slDistance * config.breakEvenAtR) {
            breakEvenActive = true;
            stopLoss = entryPrice + entryPrice * 2 * config.takerFeePct;
          }
          // Indicator-based exit
          if (!exitedThisBar && strategy.shouldExit?.(candle, side, entryPrice, barsHeld)) {
            closeTrade(candle.close, "signal", candle);
            exitedThisBar = true;
          }
          // Time exit
          if (!exitedThisBar && config.timeExitBars > 0 && barsHeld >= config.timeExitBars) {
            closeTrade(candle.close, "time", candle);
            exitedThisBar = true;
          }
        }
      } else {
        // SHORT
        if (candle.high >= stopLoss) {
          closeTrade(stopLoss, breakEvenActive ? "be" : "sl", candle);
          exitedThisBar = true;
        }
        else if (config.rrRatio > 0 && candle.low <= takeProfit) {
          closeTrade(takeProfit, "tp", candle);
          exitedThisBar = true;
        }
        else {
          if (!breakEvenActive && config.breakEvenAtR < 90 && candle.low <= entryPrice - slDistance * config.breakEvenAtR) {
            breakEvenActive = true;
            stopLoss = entryPrice - entryPrice * 2 * config.takerFeePct;
          }
          if (!exitedThisBar && strategy.shouldExit?.(candle, side, entryPrice, barsHeld)) {
            closeTrade(candle.close, "signal", candle);
            exitedThisBar = true;
          }
          if (!exitedThisBar && config.timeExitBars > 0 && barsHeld >= config.timeExitBars) {
            closeTrade(candle.close, "time", candle);
            exitedThisBar = true;
          }
        }
      }
    }

    // ── 3. If flat AND didn't just exit: check for entry ────────────────
    if (!inPosition && !exitedThisBar && capital > 0) {
      if (signal.direction === "none" || signal.atr <= 0) continue;

      let rawSlDist = config.slAtrMult * signal.atr;
      const minSl = candle.close * 0.0015;
      const maxSl = candle.close * 0.01;
      rawSlDist = Math.max(minSl, Math.min(maxSl, rawSlDist));

      const risk = capital * config.riskPct;
      let size = risk / rawSlDist;
      const notional = size * candle.close;
      if (notional / capital > config.maxLeverage) {
        size = (capital * config.maxLeverage) / candle.close;
      }
      if (size <= 0) continue;

      const isBuy = signal.direction === "long";
      const entry = fp(candle.close, isBuy);
      const fee = size * entry * config.takerFeePct;

      inPosition = true;
      side = signal.direction;
      entryPrice = entry;
      posSize = size;
      slDistance = rawSlDist;
      entryTime = candle.time;
      entryBar = barCount;
      breakEvenActive = false;
      entryFee = fee;
      riskAmount = risk;

      if (side === "long") {
        stopLoss = entry - rawSlDist;
        takeProfit = entry + rawSlDist * config.rrRatio;
      } else {
        stopLoss = entry + rawSlDist;
        takeProfit = entry - rawSlDist * config.rrRatio;
      }
    }
  }

  if (inPosition && candles.length > 0) {
    closeTrade(candles[candles.length - 1].close, "end", candles[candles.length - 1]);
  }

  // ── Metrics ───────────────────────────────────────────────────────────────
  const wins = trades.filter((t) => t.netPnl > 0);
  const losses = trades.filter((t) => t.netPnl < 0);
  const breakEvens = trades.filter((t) => t.netPnl === 0);
  const totalFees = trades.reduce((s, t) => s + t.fees, 0);
  const totalPnl = capital - config.initialCapital;
  const grossWins = wins.reduce((s, t) => s + t.netPnl, 0);
  const grossLosses = Math.abs(losses.reduce((s, t) => s + t.netPnl, 0));

  // R-multiples per trade
  const winRs = wins.map((t) => t.riskAmount > 0 ? t.netPnl / t.riskAmount : 0);
  const lossRs = losses.map((t) => t.riskAmount > 0 ? Math.abs(t.netPnl) / t.riskAmount : 0);
  const allRs = trades.map((t) => t.riskAmount > 0 ? t.netPnl / t.riskAmount : 0);

  const avgWinR = winRs.length > 0 ? winRs.reduce((a, b) => a + b, 0) / winRs.length : 0;
  const avgLossR = lossRs.length > 0 ? lossRs.reduce((a, b) => a + b, 0) / lossRs.length : 0;
  const expectancyR = allRs.length > 0 ? allRs.reduce((a, b) => a + b, 0) / allRs.length : 0;

  const dailyReturns = computeDailyReturns(trades, config.initialCapital);
  const sharpe = computeSharpe(dailyReturns);
  const avgDuration = trades.length > 0
    ? trades.reduce((s, t) => s + t.barsHeld, 0) / trades.length
    : 0;

  return {
    totalPnl, totalPnlPct: (totalPnl / config.initialCapital) * 100,
    totalFees, numTrades: trades.length,
    winRate: trades.length > 0 ? (wins.length / trades.length) * 100 : 0,
    wins: wins.length, losses: losses.length, breakEvens: breakEvens.length,
    avgWinR, avgLossR, expectancyR,
    profitFactor: grossLosses > 0 ? grossWins / grossLosses : grossWins > 0 ? Infinity : 0,
    maxDrawdown, maxDrawdownPct: peakCapital > 0 ? (maxDrawdown / peakCapital) * 100 : 0,
    sharpeRatio: sharpe, avgTradeDuration: avgDuration,
    finalCapital: capital, trades,
  };
}

function computeDailyReturns(trades: Trade[], initialCapital: number): number[] {
  if (trades.length === 0) return [];
  const dailyPnl = new Map<string, number>();
  for (const t of trades) {
    const day = new Date(t.exitTime).toISOString().slice(0, 10);
    dailyPnl.set(day, (dailyPnl.get(day) ?? 0) + t.netPnl);
  }
  let running = initialCapital;
  const returns: number[] = [];
  for (const pnl of dailyPnl.values()) {
    returns.push(running > 0 ? pnl / running : 0);
    running += pnl;
  }
  return returns;
}

function computeSharpe(returns: number[]): number {
  if (returns.length < 2) return 0;
  const mean = returns.reduce((a, b) => a + b, 0) / returns.length;
  const variance = returns.reduce((s, r) => s + (r - mean) ** 2, 0) / (returns.length - 1);
  const std = Math.sqrt(variance);
  if (std === 0) return 0;
  return (mean / std) * Math.sqrt(365);
}

export function printResult(label: string, result: BacktestResult): void {
  const pf = result.profitFactor === Infinity ? "Inf" : result.profitFactor.toFixed(2);
  const tpCount = result.trades.filter((t) => t.exitReason === "tp").length;
  const slCount = result.trades.filter((t) => t.exitReason === "sl").length;
  const beCount = result.trades.filter((t) => t.exitReason === "be").length;
  const timeCount = result.trades.filter((t) => t.exitReason === "time").length;
  const sigCount = result.trades.filter((t) => t.exitReason === "signal").length;

  console.log(`\n${"=".repeat(60)}`);
  console.log(`  ${label}`);
  console.log(`${"=".repeat(60)}`);
  console.log(`  Capital:     $${result.finalCapital.toFixed(2)} (from $${(result.finalCapital - result.totalPnl).toFixed(2)})`);
  console.log(`  Total P&L:   $${result.totalPnl.toFixed(2)} (${result.totalPnlPct >= 0 ? "+" : ""}${result.totalPnlPct.toFixed(2)}%)`);
  console.log(`  Total Fees:  $${result.totalFees.toFixed(4)}`);
  console.log(`  Trades:      ${result.numTrades} (${result.wins}W / ${result.losses}L / ${result.breakEvens}BE)`);
  console.log(`  Win Rate:    ${result.winRate.toFixed(1)}%`);
  console.log(`  Avg Win:     ${result.avgWinR.toFixed(2)}R | Avg Loss: ${result.avgLossR.toFixed(2)}R`);
  console.log(`  Expectancy:  ${result.expectancyR >= 0 ? "+" : ""}${result.expectancyR.toFixed(3)}R per trade`);
  console.log(`  Profit Factor: ${pf}`);
  console.log(`  Max DD:      $${result.maxDrawdown.toFixed(2)} (${result.maxDrawdownPct.toFixed(2)}%)`);
  console.log(`  Sharpe:      ${result.sharpeRatio.toFixed(2)}`);
  console.log(`  Avg Duration: ${result.avgTradeDuration.toFixed(1)} bars`);
  console.log(`  Exits:       TP=${tpCount} SL=${slCount} BE=${beCount} Time=${timeCount} Sig=${sigCount}`);
  console.log(`${"=".repeat(60)}\n`);
}
