import type { NordUser } from "@n1xyz/nord-ts";
import Decimal from "decimal.js";
import { BinancePriceFeed } from "../../../../../exchanges/typescript/binance-feed.js";
import { FairPriceCalculator, type FairPriceConfig, type FairPriceProvider } from "../../pricing/fair-price.js";
import { AccountStream, type FillEvent, createZoClient, type ZoClient, ZoOrderbookStream, type CachedOrder, cancelOrders, updateQuotes } from "../../../../../exchanges/typescript/zerone.js";
import type { MidPrice } from "../../../core/types.js";
import { log } from "../../../core/utils/logger.js";
import type { MarketMakerConfig } from "./config.js";
import { type PositionConfig, PositionTracker } from "./position.js";
import { Quoter } from "./quoter.js";

export type { MarketMakerConfig } from "./config.js";

interface ApiOrder {
  orderId: bigint | number;
  marketId: number;
  side: "bid" | "ask";
  price: number | string;
  size: number | string;
}

function mapApiOrdersToCached(orders: ApiOrder[]): CachedOrder[] {
  return orders.map((o) => ({
    orderId: o.orderId.toString(),
    side: o.side,
    price: new Decimal(o.price),
    size: new Decimal(o.size),
  }));
}

function deriveBinanceSymbol(marketSymbol: string): string {
  const base = marketSymbol.replace(/-PERP$/i, "").replace(/USD$/i, "").toLowerCase();
  return `${base}usdt`;
}

export class MarketMaker {
  private client: ZoClient | null = null;
  private marketId = 0;
  private marketSymbol = "";
  private accountStream: AccountStream | null = null;
  private orderbookStream: ZoOrderbookStream | null = null;
  private binanceFeed: BinancePriceFeed | null = null;
  private fairPriceCalc: FairPriceProvider | null = null;
  private positionTracker: PositionTracker | null = null;
  private quoter: Quoter | null = null;
  private isRunning = false;
  private lastLoggedSampleCount = -1;
  private activeOrders: CachedOrder[] = [];
  private isUpdating = false;
  private updateThrottle: ReturnType<typeof setTimeout> | null = null;
  private lastUpdateTime = 0;
  private statusInterval: ReturnType<typeof setInterval> | null = null;
  private orderSyncInterval: ReturnType<typeof setInterval> | null = null;
  // Risk tracking
  private entryCapital = 0;  // USDC balance at start

  constructor(
    private readonly config: MarketMakerConfig,
    private readonly privateKey: string,
  ) {}

  private requireClient(): ZoClient {
    if (!this.client) throw new Error("Client not initialized");
    return this.client;
  }

  async run(): Promise<void> {
    log.banner();
    await this.initialize();
    this.setupEventHandlers();
    await this.syncInitialOrders();
    this.startIntervals();
    this.registerShutdownHandlers();
    log.info("Warming up price feeds...");
    await new Promise(() => {});
  }

  private async initialize(): Promise<void> {
    this.client = await createZoClient(this.privateKey);
    const { nord, accountId, user } = this.client;

    // Record starting capital for loss tracking
    const balances = user.balances[accountId] || [];
    const usdc = balances.find((b) => b.symbol === "USDC");
    this.entryCapital = usdc?.balance ?? 0;
    log.info(`Starting capital: $${this.entryCapital.toFixed(2)} USDC`);

    // Find market
    const market = nord.markets.find((m) =>
      m.symbol.toUpperCase().startsWith(this.config.symbol.toUpperCase()),
    );
    if (!market) {
      const available = nord.markets.map((m) => m.symbol).join(", ");
      throw new Error(`Market "${this.config.symbol}" not found. Available: ${available}`);
    }
    this.marketId = market.marketId;
    this.marketSymbol = market.symbol;

    const binanceSymbol = deriveBinanceSymbol(market.symbol);

    const fairPriceConfig: FairPriceConfig = {
      windowMs: this.config.fairPriceWindowMs,
      minSamples: this.config.warmupSeconds,
    };
    const positionConfig: PositionConfig = {
      closeThresholdUsd: this.config.closeThresholdUsd,
      syncIntervalMs: this.config.positionSyncIntervalMs,
    };

    this.fairPriceCalc = new FairPriceCalculator(fairPriceConfig);
    this.positionTracker = new PositionTracker(positionConfig);
    this.quoter = new Quoter(
      market.priceDecimals,
      market.sizeDecimals,
      this.config.spreadBps,
      this.config.takeProfitBps,
      this.config.orderSizeUsd,
      this.config.minSpreadBps,
    );

    this.accountStream = new AccountStream(nord, accountId);
    this.orderbookStream = new ZoOrderbookStream(nord, this.marketSymbol);
    this.binanceFeed = new BinancePriceFeed(binanceSymbol);
    this.isRunning = true;

    log.config({
      Market: this.marketSymbol,
      Binance: binanceSymbol,
      Spread: `${this.config.spreadBps} bps`,
      "Take Profit": `${this.config.takeProfitBps} bps`,
      "Order Size": `$${this.config.orderSizeUsd}`,
      "Close Mode at": `>=$${this.config.closeThresholdUsd}`,
      "Max Capital": `$${this.config.maxCapitalUsd}`,
      "Max Loss": `$${this.config.maxPositionLossUsd}`,
    });
  }

  private setupEventHandlers(): void {
    const { user, accountId } = this.requireClient();

    this.accountStream?.syncOrders(user, accountId);
    this.accountStream?.setOnFill((fill: FillEvent) => {
      log.fill(fill.side === "bid" ? "buy" : "sell", fill.price, fill.size);
      this.positionTracker?.applyFill(fill.side, fill.size, fill.price);
      if (this.positionTracker?.isCloseMode(fill.price)) {
        this.cancelOrdersAsync();
      }
    });

    if (this.binanceFeed) this.binanceFeed.onPrice = (p) => this.handleBinancePrice(p);
    if (this.orderbookStream) this.orderbookStream.onPrice = (p) => this.handleZoPrice(p);

    this.accountStream?.connect();
    this.orderbookStream?.connect();
    this.binanceFeed?.connect();
  }

  private handleBinancePrice(binancePrice: MidPrice): void {
    const zoPrice = this.orderbookStream?.getMidPrice();
    if (zoPrice && Math.abs(binancePrice.timestamp - zoPrice.timestamp) < 1000) {
      this.fairPriceCalc?.addSample(zoPrice.mid, binancePrice.mid);
    }

    if (!this.isRunning) return;

    const fairPrice = this.fairPriceCalc?.getFairPrice(binancePrice.mid);
    if (!fairPrice) {
      this.logWarmupProgress(binancePrice);
      return;
    }

    if (this.lastLoggedSampleCount < this.config.warmupSeconds) {
      this.lastLoggedSampleCount = this.config.warmupSeconds;
      log.info(`Ready! Fair price: $${fairPrice.toFixed(2)}`);
    }

    this.throttledUpdate(fairPrice);
  }

  private handleZoPrice(zoPrice: MidPrice): void {
    const binancePrice = this.binanceFeed?.getMidPrice();
    if (binancePrice && Math.abs(zoPrice.timestamp - binancePrice.timestamp) < 1000) {
      this.fairPriceCalc?.addSample(zoPrice.mid, binancePrice.mid);
    }
  }

  private throttledUpdate(fairPrice: number): void {
    const now = Date.now();
    const elapsed = now - this.lastUpdateTime;
    if (elapsed >= this.config.updateThrottleMs) {
      this.lastUpdateTime = now;
      void this.executeUpdate(fairPrice);
    } else if (!this.updateThrottle) {
      this.updateThrottle = setTimeout(() => {
        this.updateThrottle = null;
        this.lastUpdateTime = Date.now();
        void this.executeUpdate(fairPrice);
      }, this.config.updateThrottleMs - elapsed);
    }
  }

  private logWarmupProgress(binancePrice: MidPrice): void {
    const state = this.fairPriceCalc?.getState();
    if (!state || state.samples === this.lastLoggedSampleCount) return;
    this.lastLoggedSampleCount = state.samples;
    const zoPrice = this.orderbookStream?.getMidPrice();
    log.info(
      `Warming up: ${state.samples}/${this.config.warmupSeconds} samples | Binance $${binancePrice.mid.toFixed(2)} | 01 $${zoPrice?.mid.toFixed(2) ?? "--"}`,
    );
  }

  private async syncInitialOrders(): Promise<void> {
    const { user, accountId } = this.requireClient();
    await user.fetchInfo();
    const existingOrders = (user.orders[accountId] ?? []) as ApiOrder[];
    const marketOrders = existingOrders.filter((o) => o.marketId === this.marketId);
    this.activeOrders = mapApiOrdersToCached(marketOrders);
    if (this.activeOrders.length > 0) {
      log.info(`Synced ${this.activeOrders.length} existing orders`);
    }
    this.positionTracker?.startSync(user, accountId, this.marketId);
  }

  private startIntervals(): void {
    const { user, accountId } = this.requireClient();
    this.statusInterval = setInterval(() => this.logStatus(), this.config.statusIntervalMs);
    this.orderSyncInterval = setInterval(
      () => this.syncOrders(user, accountId),
      this.config.orderSyncIntervalMs,
    );
  }

  private registerShutdownHandlers(): void {
    const shutdown = () => void this.shutdown();
    process.on("SIGINT", shutdown);
    process.on("SIGTERM", shutdown);
  }

  private async shutdown(): Promise<void> {
    log.shutdown();
    this.isRunning = false;
    this.positionTracker?.stopSync();
    if (this.updateThrottle) { clearTimeout(this.updateThrottle); this.updateThrottle = null; }
    if (this.statusInterval) { clearInterval(this.statusInterval); this.statusInterval = null; }
    if (this.orderSyncInterval) { clearInterval(this.orderSyncInterval); this.orderSyncInterval = null; }
    this.binanceFeed?.close();
    this.orderbookStream?.close();
    this.accountStream?.close();
    try {
      if (this.activeOrders.length > 0 && this.client) {
        await cancelOrders(this.client.user, this.activeOrders);
        log.info(`Cancelled ${this.activeOrders.length} orders. Goodbye!`);
      } else {
        log.info("No active orders. Goodbye!");
      }
    } catch (err) {
      log.error("Shutdown error:", err);
    }
    process.exit(0);
  }

  private async executeUpdate(fairPrice: number): Promise<void> {
    if (this.isUpdating) return;

    // ── Risk check: max loss guard ────────────────────────────────
    if (this.client) {
      try {
        const { user, accountId } = this.client;
        const balances = user.balances[accountId] || [];
        const usdc = balances.find((b) => b.symbol === "USDC");
        const currentCapital = usdc?.balance ?? this.entryCapital;
        const loss = this.entryCapital - currentCapital;
        if (loss >= this.config.maxPositionLossUsd) {
          if (this.isRunning) {
            log.warn(`⛔ Max loss reached ($${loss.toFixed(2)}). Stopping bot.`);
            await this.shutdown();
          }
          return;
        }
      } catch {
        // Don't crash on balance check failure, just skip
      }
    }

    this.isUpdating = true;
    try {
      if (!this.positionTracker || !this.quoter || !this.client) return;

      const quotingCtx = this.positionTracker.getQuotingContext(fairPrice);
      const { positionState } = quotingCtx;

      if (positionState.sizeBase !== 0) {
        log.position(positionState.sizeBase, positionState.sizeUsd, positionState.isLong, positionState.isCloseMode);
      }

      const bbo = this.orderbookStream?.getBBO() ?? null;
      const quotes = this.quoter.getQuotes(quotingCtx, bbo);

      if (quotes.length === 0) {
        log.warn("No quotes generated");
        return;
      }

      const bid = quotes.find((q) => q.side === "bid");
      const ask = quotes.find((q) => q.side === "ask");
      const spreadBps = positionState.isCloseMode ? this.config.takeProfitBps : this.config.spreadBps;
      log.quote(
        bid?.price.toNumber() ?? null,
        ask?.price.toNumber() ?? null,
        fairPrice,
        spreadBps,
        positionState.isCloseMode ? "close" : "normal",
      );

      const newOrders = await updateQuotes(this.client.user, this.marketId, this.activeOrders, quotes);
      this.activeOrders = newOrders;
    } catch (err) {
      log.error("Update error:", err);
      this.activeOrders = [];
    } finally {
      this.isUpdating = false;
    }
  }

  private cancelOrdersAsync(): void {
    if (this.activeOrders.length === 0 || !this.client) return;
    const orders = this.activeOrders;
    cancelOrders(this.client.user, orders)
      .then(() => { this.activeOrders = []; })
      .catch((err) => { log.error("Failed to cancel orders:", err); this.activeOrders = []; });
  }

  private syncOrders(user: NordUser, accountId: number): void {
    user.fetchInfo()
      .then(() => {
        const apiOrders = (user.orders[accountId] ?? []) as ApiOrder[];
        this.activeOrders = mapApiOrdersToCached(
          apiOrders.filter((o) => o.marketId === this.marketId),
        );
      })
      .catch((err) => { log.error("Order sync error:", err); });
  }

  private logStatus(): void {
    if (!this.isRunning) return;
    const pos = this.positionTracker?.getBaseSize() ?? 0;
    const bids = this.activeOrders.filter((o) => o.side === "bid");
    const asks = this.activeOrders.filter((o) => o.side === "ask");
    const fmt = (o: CachedOrder) => `$${o.price.toFixed(2)}x${o.size}`;
    log.info(`STATUS | pos=${pos.toFixed(5)} | bid=[${bids.map(fmt).join(",") || "-"}] | ask=[${asks.map(fmt).join(",") || "-"}]`);
  }
}
