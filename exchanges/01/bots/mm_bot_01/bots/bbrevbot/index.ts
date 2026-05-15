import Decimal from "decimal.js";
import { BinancePriceFeed } from "../../../../../binance/adapters/binance-feed.js";
import { BinanceCandleFeed, type Candle } from "../../pricing/candles.js";
import { CandleAggregator } from "../../pricing/aggregator.js";
import { createZoClient, type ZoClient, ZoOrderbookStream } from "../../../../adapters/zerone.js";
import { log } from "../../../../../../shared/typescript/utils/logger.js";
import {
  calcRiskBasedSize,
  getBalance,
  getMaxLeverage,
  getServerPosition,
  placeIOCOrder,
} from "../utbot/strategy.js";
import type { BBRevBotConfig } from "./config.js";
import { BBRevIndicator, type BBRevState } from "./indicator.js";

export type { BBRevBotConfig } from "./config.js";

interface ActivePosition {
  side: "long" | "short";
  size: Decimal;
  entryPrice: number;
  entryBar: number;
  stopLoss: number;
  slDistance: number;
  breakEvenActive: boolean;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function deriveBinanceSymbol(marketSymbol: string): string {
  const base = marketSymbol.replace(/-PERP$/i, "").replace(/USD$/i, "").toLowerCase();
  return `${base}usdt`;
}

export class BBRevBot {
  private client: ZoClient | null = null;
  private marketId = 0;
  private marketSymbol = "";
  private priceDecimals = 1;
  private sizeDecimals = 3;

  private candleFeed: BinanceCandleFeed | null = null;
  private priceFeed: BinancePriceFeed | null = null;
  private orderbookStream: ZoOrderbookStream | null = null;
  private indicator: BBRevIndicator | null = null;
  private aggregator: CandleAggregator | null = null;

  private isRunning = false;
  private isExecuting = false;

  private activePosition: ActivePosition | null = null;
  private barCount = 0;
  private lastState: BBRevState | null = null;

  private statusInterval: NodeJS.Timeout | null = null;
  private syncInterval: NodeJS.Timeout | null = null;

  private entryCapital = 0;
  private signalCount = 0;
  private tradeCount = 0;
  private winCount = 0;
  private lossCount = 0;
  private beCount = 0;

  constructor(
    private readonly config: BBRevBotConfig,
    private readonly privateKey: string,
  ) {}

  async run(): Promise<void> {
    this.printBanner();
    await this.initialize();
    await this.warmUp();
    this.isRunning = true;
    this.startIntervals();
    this.registerShutdownHandlers();
    log.info(`BB Rev Bot is LIVE. Trading on ${this.config.candleMinutes}m candles...`);
    await new Promise<never>(() => {});
  }

  // ── Initialization ──────────────────────────────────────────────────────────

  private async initialize(): Promise<void> {
    this.client = await createZoClient(this.privateKey);
    const { nord, accountId, user } = this.client;

    const balances = user.balances[accountId] ?? [];
    const usdc = balances.find((b) => b.symbol === "USDC");
    this.entryCapital = usdc?.balance ?? 0;
    log.info(`Starting capital: $${this.entryCapital.toFixed(2)} USDC`);

    const market = nord.markets.find((m) =>
      m.symbol.toUpperCase().startsWith(this.config.symbol.toUpperCase()),
    );
    if (!market) {
      const available = nord.markets.map((m) => m.symbol).join(", ");
      throw new Error(`Market "${this.config.symbol}" not found. Available: ${available}`);
    }
    this.marketId = market.marketId;
    this.marketSymbol = market.symbol;
    this.priceDecimals = market.priceDecimals;
    this.sizeDecimals = market.sizeDecimals;

    const binanceSymbol = deriveBinanceSymbol(market.symbol);
    const cfg = this.config;

    this.indicator = new BBRevIndicator(
      cfg.bbPeriod, cfg.bbMult, cfg.emaTrendPeriod, cfg.atrPeriod,
    );

    this.aggregator = new CandleAggregator(cfg.candleMinutes);

    this.candleFeed = new BinanceCandleFeed(binanceSymbol, cfg.candleHistoryLength);
    this.priceFeed = new BinancePriceFeed(binanceSymbol);
    this.orderbookStream = new ZoOrderbookStream(nord, this.marketSymbol);

    log.config({
      Strategy: "BB Mean Reversion (buy dips, sell rallies at BB extremes)",
      Market: this.marketSymbol,
      "Binance feed": `${binanceSymbol.toUpperCase()} 1m → ${cfg.candleMinutes}m`,
      "BB": `SMA(${cfg.bbPeriod}), ${cfg.bbMult}σ`,
      "Trend filter": cfg.emaTrendPeriod > 0 ? `EMA(${cfg.emaTrendPeriod})` : "disabled",
      "Exit": "Signal: price crosses BB middle (SMA20)",
      "Risk per trade": `${(cfg.riskPct * 100).toFixed(1)}% of balance`,
      "SL": `${cfg.slAtrMult}×ATR(${cfg.atrPeriod}) (safety net)`,
      "Break-even": "fee-based (move SL when profit > round-trip fees)",
      "Time exit": `${cfg.timeExitBars} bars`,
    });
  }

  // ── Warmup ──────────────────────────────────────────────────────────────────

  private async warmUp(): Promise<void> {
    log.info("Loading historical candles and warming up indicators...");
    await this.candleFeed!.loadHistory();

    const history1m = this.candleFeed!.getCandles();
    const historyAgg = this.aggregator!.warmup(history1m);

    let readyCount = 0;
    for (const candle of historyAgg) {
      const state = this.indicator!.update(candle);
      if (state !== null) {
        readyCount++;
        this.lastState = state;
      }
    }
    log.info(
      `Aggregated ${history1m.length} 1m candles → ${historyAgg.length} ${this.config.candleMinutes}m candles. ` +
      `${readyCount} indicator-ready.`,
    );

    const { user, accountId } = this.client!;
    const serverPos = await getServerPosition(user, accountId, this.marketId);
    if (serverPos.size > 0) {
      log.warn(
        `Existing position found: ${serverPos.isLong ? "LONG" : "SHORT"} ${serverPos.size}. ` +
        "BB Rev Bot cannot track pre-existing entry — exits via time or signal only.",
      );
    } else {
      log.info("No existing position. Starting flat.");
    }

    void this.orderbookStream!.connect();
    this.priceFeed!.connect();
    this.candleFeed!.connect();

    // 1m candles from WS → aggregate → process on N-minute close
    this.candleFeed!.onCandle = (candle1m) => {
      const completed = this.aggregator!.update(candle1m);
      if (completed) {
        void this.handleCandleClose(completed);
      }
    };

    this.priceFeed!.onPrice = (price) => { this.handlePriceUpdate(price.bid, price.ask); };
    log.info("All feeds connected. Waiting for next candle close...");
  }

  // ── Candle handler ──────────────────────────────────────────────────────────

  private async handleCandleClose(candle: Candle): Promise<void> {
    this.barCount++;
    const state = this.indicator!.update(candle);
    if (!state) return;
    this.lastState = state;

    this.logCandleState(state);

    if (this.isExecuting) {
      log.warn("Candle skipped: previous execution still in progress");
      return;
    }

    // ── Exit checks (indicator exit + time exit) ──────────────────────────
    if (this.activePosition) {
      const pos = this.activePosition;
      const barsHeld = this.barCount - pos.entryBar;

      // Indicator exit: price crossed BB middle (mean reversion target)
      const sigExit = (pos.side === "long" && state.exitLong)
        || (pos.side === "short" && state.exitShort);

      if (sigExit) {
        log.info(`>>> SIGNAL EXIT: price $${state.price.toFixed(2)} crossed BB mid $${state.bbMid.toFixed(2)}`);
        await this.closePosition("signal (BB mid)");
        return;
      }

      // Time exit
      if (barsHeld >= this.config.timeExitBars) {
        log.info(`>>> TIME EXIT: ${barsHeld} bars >= ${this.config.timeExitBars}`);
        await this.closePosition(`time exit (${barsHeld} bars)`);
        return;
      }
    }

    // ── Entry checks (only when flat) ────────────────────────────────────
    if (!this.activePosition) {
      if (state.longSignal) {
        this.signalCount++;
        log.info(
          `>>> SIGNAL #${this.signalCount}: LONG @ $${state.price.toFixed(2)} | ` +
          `BB_lower=$${state.bbLower.toFixed(2)} | BB_mid=$${state.bbMid.toFixed(2)}`,
        );
        await this.openPosition("long", state.price, state.atr);
      } else if (state.shortSignal) {
        this.signalCount++;
        log.info(
          `>>> SIGNAL #${this.signalCount}: SHORT @ $${state.price.toFixed(2)} | ` +
          `BB_upper=$${state.bbUpper.toFixed(2)} | BB_mid=$${state.bbMid.toFixed(2)}`,
        );
        await this.openPosition("short", state.price, state.atr);
      }
    }
  }

  // ── Real-time price monitoring (SL + Break-even) ──────────────────────────

  private handlePriceUpdate(bid: number, ask: number): void {
    if (!this.isRunning || !this.activePosition || this.isExecuting) return;
    const pos = this.activePosition;

    const checkPrice = pos.side === "long" ? bid : ask;

    // ── Fee-based break-even activation ─────────────────────────────────
    if (!pos.breakEvenActive) {
      const roundTripFees = pos.entryPrice * 2 * this.config.takerFeePct;
      const beReached = pos.side === "long"
        ? checkPrice >= pos.entryPrice + roundTripFees
        : checkPrice <= pos.entryPrice - roundTripFees;

      if (beReached) {
        pos.breakEvenActive = true;
        pos.stopLoss = pos.side === "long"
          ? pos.entryPrice * (1 + 2 * this.config.takerFeePct)
          : pos.entryPrice * (1 - 2 * this.config.takerFeePct);
        log.info(
          `[BE] Break-even activated → SL moved to $${pos.stopLoss.toFixed(this.priceDecimals)} ` +
          `(covers round-trip fees)`,
        );
      }
    }

    // ── Stop-loss check ─────────────────────────────────────────────────
    const slHit = pos.side === "long"
      ? checkPrice <= pos.stopLoss
      : checkPrice >= pos.stopLoss;

    if (slHit) {
      const label = pos.breakEvenActive ? "BREAK-EVEN STOP" : "STOP LOSS";
      void this.executeStop(label, checkPrice);
    }
  }

  // ── Execute SL close ──────────────────────────────────────────────────────

  private async executeStop(reason: string, triggerPrice: number): Promise<void> {
    if (this.isExecuting || !this.activePosition) return;
    this.isExecuting = true;

    const pos = this.activePosition;
    log.warn(
      `[${reason}] @ $${triggerPrice.toFixed(this.priceDecimals)} | ` +
      `entry=$${pos.entryPrice.toFixed(this.priceDecimals)} | side=${pos.side.toUpperCase()}`,
    );

    try {
      const { user, accountId } = this.client!;
      const bbo = this.orderbookStream?.getBBO();
      const binance = this.priceFeed?.getMidPrice();
      const askRef = bbo?.bestAsk ?? binance?.ask ?? triggerPrice * 1.001;
      const bidRef = bbo?.bestBid ?? binance?.bid ?? triggerPrice * 0.999;
      const priceRef = pos.side === "long" ? bidRef : askRef;
      const direction = pos.side === "long" ? "sell" : "buy";

      const submitted = await placeIOCOrder(
        user, this.marketId, direction, priceRef, pos.size,
        this.priceDecimals, this.sizeDecimals,
        this.config.slippageBps, true,
      );

      if (!submitted) {
        log.error(`[${reason}] Close order failed!`);
        return;
      }

      await sleep(this.config.fillVerifyDelayMs);
      const serverPos = await getServerPosition(user, accountId, this.marketId);

      if (pos.breakEvenActive) {
        this.beCount++;
      } else {
        this.lossCount++;
      }

      if (serverPos.size === 0) {
        const barsHeld = this.barCount - pos.entryBar;
        const wr = this.tradeCount > 0 ? ((this.winCount / this.tradeCount) * 100).toFixed(1) : "0.0";
        log.info(
          `[${reason}] Closed ${pos.side.toUpperCase()} | held ${barsHeld} bars | ` +
          `W=${this.winCount} L=${this.lossCount} BE=${this.beCount} WR=${wr}%`,
        );
      } else {
        log.warn(`Position not fully closed (remaining: ${serverPos.size}).`);
      }
      this.activePosition = null;
    } catch (err) {
      log.error(`[${reason}] error:`, err);
    } finally {
      this.isExecuting = false;
    }
  }

  // ── Open position ─────────────────────────────────────────────────────────

  private async openPosition(side: "long" | "short", price: number, atr: number): Promise<void> {
    this.isExecuting = true;
    try {
      const { user, accountId } = this.client!;
      const balance = await getBalance(user, accountId);
      if (balance <= 0) {
        log.warn("Balance is zero — cannot open position");
        return;
      }

      const cfg = this.config;
      const slDistance = cfg.slAtrMult * atr;

      const minSl = price * 0.0015;
      const maxSl = price * 0.01;
      const clampedSl = Math.max(minSl, Math.min(maxSl, slDistance));

      const maxLev = getMaxLeverage(cfg.symbol);
      const size = calcRiskBasedSize(balance, cfg.riskPct, clampedSl, price, this.sizeDecimals, maxLev);
      if (size.lte(0)) {
        log.warn(`Position size rounds to zero`);
        return;
      }

      const stopLoss = side === "long" ? price - clampedSl : price + clampedSl;

      const bbo = this.orderbookStream?.getBBO();
      const binance = this.priceFeed?.getMidPrice();
      const askRef = bbo?.bestAsk ?? binance?.ask ?? price * 1.001;
      const bidRef = bbo?.bestBid ?? binance?.bid ?? price * 0.999;
      const priceRef = side === "long" ? askRef : bidRef;
      const direction = side === "long" ? "buy" : "sell";

      const notional = size.toNumber() * price;
      const impliedLeverage = (notional / balance).toFixed(1);
      const riskUsd = (balance * cfg.riskPct).toFixed(4);

      log.info(
        `Opening ${side.toUpperCase()} ${size} | ` +
        `risk=$${riskUsd} (${(cfg.riskPct * 100).toFixed(1)}%) | ` +
        `notional=$${notional.toFixed(2)} (${impliedLeverage}x) | ` +
        `SL=$${stopLoss.toFixed(this.priceDecimals)} | ` +
        `exit=BB middle (signal)`,
      );

      const submitted = await placeIOCOrder(
        user, this.marketId, direction, priceRef, size,
        this.priceDecimals, this.sizeDecimals,
        cfg.slippageBps, false,
      );

      if (!submitted) {
        log.warn("Open order submission failed");
        return;
      }

      await sleep(cfg.fillVerifyDelayMs);
      const serverPos = await getServerPosition(user, accountId, this.marketId);

      if (serverPos.size > 0) {
        this.activePosition = {
          side,
          size: new Decimal(serverPos.size),
          entryPrice: price,
          entryBar: this.barCount,
          stopLoss,
          slDistance: clampedSl,
          breakEvenActive: false,
        };
        this.tradeCount++;
        log.info(
          `[TRADE #${this.tradeCount}] ${side.toUpperCase()} confirmed | ` +
          `SL=$${stopLoss.toFixed(this.priceDecimals)} | exit=BB mid`,
        );
      } else {
        log.warn("IOC open order did not fill — staying flat.");
      }
    } catch (err) {
      log.error("openPosition error:", err);
    } finally {
      this.isExecuting = false;
    }
  }

  // ── Close position (for signal/time exits) ────────────────────────────────

  private async closePosition(reason: string): Promise<void> {
    if (!this.activePosition) return;
    this.isExecuting = true;
    try {
      const { user, accountId } = this.client!;
      const pos = this.activePosition;

      const bbo = this.orderbookStream?.getBBO();
      const binance = this.priceFeed?.getMidPrice();
      const askRef = bbo?.bestAsk ?? binance?.ask ?? pos.entryPrice * 1.001;
      const bidRef = bbo?.bestBid ?? binance?.bid ?? pos.entryPrice * 0.999;
      const priceRef = pos.side === "long" ? bidRef : askRef;
      const direction = pos.side === "long" ? "sell" : "buy";

      log.info(`Closing ${pos.side.toUpperCase()} ${pos.size} (${reason})...`);

      const submitted = await placeIOCOrder(
        user, this.marketId, direction, priceRef, pos.size,
        this.priceDecimals, this.sizeDecimals,
        this.config.slippageBps, true,
      );

      if (!submitted) {
        log.warn("Close order submission failed");
        return;
      }

      await sleep(this.config.fillVerifyDelayMs);
      const serverPos = await getServerPosition(user, accountId, this.marketId);

      if (serverPos.size === 0) {
        const barsHeld = this.barCount - pos.entryBar;
        // Track outcome
        if (pos.breakEvenActive) {
          this.beCount++;
        } else {
          const favorable = pos.side === "long"
            ? priceRef > pos.entryPrice
            : priceRef < pos.entryPrice;
          if (favorable) this.winCount++;
          else this.lossCount++;
        }
        const wr = this.tradeCount > 0 ? ((this.winCount / this.tradeCount) * 100).toFixed(1) : "0.0";
        log.info(
          `[EXIT] ${pos.side.toUpperCase()} closed via ${reason} | held ${barsHeld} bars | ` +
          `W=${this.winCount} L=${this.lossCount} BE=${this.beCount} WR=${wr}%`,
        );
      } else {
        log.warn(`Position not fully closed. Clearing state.`);
      }
      this.activePosition = null;
    } catch (err) {
      log.error("closePosition error:", err);
    } finally {
      this.isExecuting = false;
    }
  }

  // ── Logging ───────────────────────────────────────────────────────────────

  private logCandleState(state: BBRevState): void {
    const posStr = this.activePosition
      ? `${this.activePosition.side.toUpperCase()} ${this.activePosition.size.toFixed(this.sizeDecimals)}`
      : "FLAT";
    const emaStr = state.ema !== null ? `EMA=${state.ema.toFixed(2)}` : "EMA=n/a";

    log.info(
      `BAR#${this.barCount} $${state.price.toFixed(2)} | ` +
      `BB[${state.bbLower.toFixed(2)}|${state.bbMid.toFixed(2)}|${state.bbUpper.toFixed(2)}] | ` +
      `${emaStr} | ATR=${state.atr.toFixed(2)} | pos=${posStr}`,
    );
  }

  // ── Intervals ─────────────────────────────────────────────────────────────

  private startIntervals(): void {
    this.statusInterval = setInterval(() => {
      if (!this.isRunning) return;
      const pos = this.activePosition;
      const posStr = pos
        ? `${pos.side.toUpperCase()} ${pos.size.toFixed(this.sizeDecimals)} (entry=$${pos.entryPrice.toFixed(2)} SL=$${pos.stopLoss.toFixed(this.priceDecimals)}${pos.breakEvenActive ? " [BE]" : ""})`
        : "FLAT";
      const wr = this.tradeCount > 0 ? ((this.winCount / this.tradeCount) * 100).toFixed(1) : "0.0";
      log.info(`STATUS | ${posStr} | signals=${this.signalCount} trades=${this.tradeCount} W=${this.winCount} L=${this.lossCount} BE=${this.beCount} WR=${wr}%`);
    }, this.config.statusIntervalMs);

    const { user, accountId } = this.client!;
    this.syncInterval = setInterval(() => {
      getServerPosition(user, accountId, this.marketId)
        .then((serverPos) => {
          if (this.isExecuting) return;
          const serverHasPos = serverPos.size > 0;
          const localHasPos = this.activePosition !== null;

          if (!serverHasPos && localHasPos) {
            log.warn("Drift: server is flat but local tracks a position. Clearing.");
            this.activePosition = null;
          } else if (serverHasPos && localHasPos) {
            const serverSide = serverPos.isLong ? "long" : "short";
            if (serverSide !== this.activePosition!.side) {
              log.warn(`Drift: server=${serverSide}, local=${this.activePosition!.side}. Clearing.`);
              this.activePosition = null;
            }
          }
        })
        .catch((err) => { log.error("Position sync error:", err); });
    }, this.config.positionSyncIntervalMs);
  }

  // ── Shutdown ──────────────────────────────────────────────────────────────

  private registerShutdownHandlers(): void {
    const shutdown = () => void this.shutdown();
    process.on("SIGINT", shutdown);
    process.on("SIGTERM", shutdown);
  }

  private async shutdown(): Promise<void> {
    log.info("Shutting down BB Rev Bot...");
    this.isRunning = false;
    if (this.statusInterval) { clearInterval(this.statusInterval); this.statusInterval = null; }
    if (this.syncInterval) { clearInterval(this.syncInterval); this.syncInterval = null; }
    this.candleFeed?.close();
    this.priceFeed?.close();
    this.orderbookStream?.close();
    const posStr = this.activePosition
      ? `${this.activePosition.side.toUpperCase()} ${this.activePosition.size.toFixed(this.sizeDecimals)}`
      : "FLAT";
    const wr = this.tradeCount > 0 ? ((this.winCount / this.tradeCount) * 100).toFixed(1) : "0.0";
    log.info(`Final | ${posStr} | signals=${this.signalCount} trades=${this.tradeCount} W=${this.winCount} L=${this.lossCount} BE=${this.beCount} WR=${wr}%`);
    log.info("Goodbye!");
    process.exit(0);
  }

  private printBanner(): void {
    log.info(`
╔══════════════════════════════════════════════════════════╗
║    01 EXCHANGE — BB MEAN REVERSION BOT                   ║
║    Strategy: Buy BB dips, sell BB rallies                 ║
║    Exit: Signal at BB middle (mean reversion)             ║
║    Risk: 1% per trade | SL: ATR safety net                ║
╚══════════════════════════════════════════════════════════╝`);
  }
}
