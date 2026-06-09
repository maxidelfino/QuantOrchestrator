import type { Nord, OrderbookEntry, WebSocketDeltaUpdate } from "@n1xyz/nord-ts";
import type { MidPrice, PriceCallback } from "../../../../shared/typescript/types.js";
import { log } from "../../../../shared/typescript/utils/logger.js";

const RECONNECT_DELAY_MS = 3000;
const STALE_THRESHOLD_MS = 60_000;
const STALE_CHECK_INTERVAL_MS = 10_000;
const MAX_LEVELS = 100;

export interface BBO {
  bestBid: number;
  bestAsk: number;
}

export type OrderbookUpdateCallback = (
  bids: Map<number, number>,
  asks: Map<number, number>,
) => void;

class OrderbookSide {
  private levels = new Map<number, number>();
  private sortedPrices: number[] = [];
  private readonly isAsk: boolean;

  constructor(isAsk: boolean) { this.isAsk = isAsk; }

  applyDeltas(entries: OrderbookEntry[]): void {
    let needsSort = false;
    for (const { price, size } of entries) {
      if (size === 0) {
        if (this.levels.has(price)) { this.levels.delete(price); needsSort = true; }
      } else {
        if (!this.levels.has(price)) needsSort = true;
        this.levels.set(price, size);
      }
    }
    if (needsSort) this.rebuildSortedPrices();
  }

  setSnapshot(entries: OrderbookEntry[]): void {
    this.levels.clear();
    for (const { price, size } of entries) {
      if (size > 0) this.levels.set(price, size);
    }
    this.rebuildSortedPrices();
  }

  private rebuildSortedPrices(): void {
    this.sortedPrices = Array.from(this.levels.keys());
    if (this.isAsk) {
      this.sortedPrices.sort((a, b) => a - b);
    } else {
      this.sortedPrices.sort((a, b) => b - a);
    }
    if (this.sortedPrices.length > MAX_LEVELS) {
      const removed = this.sortedPrices.splice(MAX_LEVELS);
      for (const price of removed) this.levels.delete(price);
    }
  }

  getBest(): number | null {
    return this.sortedPrices.length > 0 ? this.sortedPrices[0] : null;
  }

  clear(): void { this.levels.clear(); this.sortedPrices = []; }
  size(): number { return this.levels.size; }
  getLevels(): Map<number, number> { return new Map(this.levels); }
}

export class ZoOrderbookStream {
  private subscription: ReturnType<Nord["subscribeOrderbook"]> | null = null;
  private bids = new OrderbookSide(false);
  private asks = new OrderbookSide(true);
  private latestPrice: MidPrice | null = null;
  private lastUpdateId = 0;
  private lastUpdateTime = 0;
  private isClosing = false;
  private staleCheckInterval: NodeJS.Timeout | null = null;
  private reconnectTimeout: NodeJS.Timeout | null = null;
  private snapshotLoaded = false;
  private deltaBuffer: unknown[] = [];

  onPrice: PriceCallback | null = null;
  onOrderbookUpdate: OrderbookUpdateCallback | null = null;

  constructor(
    private readonly nord: Nord,
    private readonly symbol: string,
    onPrice?: PriceCallback,
  ) {
    this.onPrice = onPrice ?? null;
  }

  async connect(): Promise<void> {
    if (this.subscription) return;
    log.info(`Subscribing to 01 orderbook (${this.symbol})...`);
    this.resetState();
    this.subscription = this.nord.subscribeOrderbook(this.symbol);
    this.setupEventHandlers();
    await this.fetchSnapshot();
    this.applyBufferedDeltas();
    this.startStaleCheck();
    log.info(`Orderbook active (${this.bids.size()} bids, ${this.asks.size()} asks)`);
  }

  private setupEventHandlers(): void {
    if (!this.subscription) return;

    this.subscription.on("message", (data: unknown) => {
      this.lastUpdateTime = Date.now();
      if (!this.snapshotLoaded) {
        this.deltaBuffer.push(data);
      } else {
        this.handleUpdate(data);
      }
    });

    this.subscription.on("error", (err: Error) => {
      log.error("Orderbook error:", err.message);
    });

    (this.subscription as unknown as { on(event: "close", cb: () => void): void }).on(
      "close", () => {
        if (!this.isClosing) {
          log.warn("Orderbook disconnected");
          this.subscription = null;
          this.scheduleReconnect();
        }
      }
    );
  }

  private async fetchSnapshot(): Promise<void> {
    try {
      const response = await this.nord.getOrderbook({ symbol: this.symbol });
      const bids = this.normalizeEntries(response.bids);
      const asks = this.normalizeEntries(response.asks);
      this.bids.setSnapshot(bids);
      this.asks.setSnapshot(asks);
      this.lastUpdateId = response.updateId;
      this.snapshotLoaded = true;
      this.emitPrice();
      log.info(`Orderbook snapshot loaded (updateId: ${response.updateId})`);
    } catch (err) {
      log.error("Failed to fetch orderbook snapshot:", err);
      throw err;
    }
  }

  private startStaleCheck(): void {
    if (this.staleCheckInterval) return;
    this.staleCheckInterval = setInterval(() => {
      if (this.isClosing) return;
      const timeSinceUpdate = Date.now() - this.lastUpdateTime;
      if (this.lastUpdateTime > 0 && timeSinceUpdate > STALE_THRESHOLD_MS) {
        log.warn(`Orderbook stale (${timeSinceUpdate}ms). Reconnecting...`);
        this.scheduleReconnect();
      }
    }, STALE_CHECK_INTERVAL_MS);
  }

  private scheduleReconnect(): void {
    if (this.reconnectTimeout) return;
    if (this.subscription) { this.subscription.close(); this.subscription = null; }
    log.info(`Reconnecting to orderbook in ${RECONNECT_DELAY_MS}ms...`);
    this.reconnectTimeout = setTimeout(() => {
      this.reconnectTimeout = null;
      void this.reconnect();
    }, RECONNECT_DELAY_MS);
  }

  private async reconnect(): Promise<void> {
    this.resetState();
    this.subscription = this.nord.subscribeOrderbook(this.symbol);
    this.setupEventHandlers();
    await this.fetchSnapshot();
    this.applyBufferedDeltas();
    log.info("Orderbook reconnected");
  }

  private resetState(): void {
    this.bids.clear();
    this.asks.clear();
    this.latestPrice = null;
    this.lastUpdateId = 0;
    this.lastUpdateTime = 0;
    this.snapshotLoaded = false;
    this.deltaBuffer = [];
  }

  private applyBufferedDeltas(): void {
    const validDeltas = this.deltaBuffer.filter((data) => {
      const d = data as { update_id?: number };
      return d.update_id !== undefined && d.update_id > this.lastUpdateId;
    });
    log.info(`Applying ${validDeltas.length} buffered deltas`);
    for (const data of validDeltas) this.handleUpdate(data);
    this.deltaBuffer = [];
  }

  private normalizeEntries(entries: unknown[] | undefined): OrderbookEntry[] {
    if (!entries || entries.length === 0) return [];
    return entries
      .map((entry) => {
        if (Array.isArray(entry)) return { price: Number(entry[0]), size: Number(entry[1]) };
        if (typeof entry === "object" && entry !== null) {
          const obj = entry as Record<string, unknown>;
          return { price: Number(obj.price), size: Number(obj.size) };
        }
        return null;
      })
      .filter((e): e is OrderbookEntry => e !== null && !Number.isNaN(e.price) && !Number.isNaN(e.size));
  }

  private handleUpdate(rawData: unknown): void {
    if (!this.snapshotLoaded) return;
    const data = rawData as WebSocketDeltaUpdate & { type?: string };
    if (data.update_id !== undefined && data.update_id <= this.lastUpdateId) return;
    if (data.update_id !== undefined) this.lastUpdateId = data.update_id;
    this.bids.applyDeltas(this.normalizeEntries(data.bids));
    this.asks.applyDeltas(this.normalizeEntries(data.asks));
    this.emitPrice();
  }

  private emitPrice(): void {
    const bestBid = this.bids.getBest();
    const bestAsk = this.asks.getBest();
    if (bestBid === null || bestAsk === null) return;
    const mid = (bestBid + bestAsk) / 2;
    this.latestPrice = { mid, bid: bestBid, ask: bestAsk, timestamp: Date.now() };
    if (this.onPrice) this.onPrice(this.latestPrice);
    if (this.onOrderbookUpdate) this.onOrderbookUpdate(this.bids.getLevels(), this.asks.getLevels());
  }

  getMidPrice(): MidPrice | null { return this.latestPrice; }

  getBBO(): BBO | null {
    const bestBid = this.bids.getBest();
    const bestAsk = this.asks.getBest();
    if (bestBid === null || bestAsk === null) return null;
    return { bestBid, bestAsk };
  }

  close(): void {
    this.isClosing = true;
    if (this.reconnectTimeout) { clearTimeout(this.reconnectTimeout); this.reconnectTimeout = null; }
    if (this.staleCheckInterval) { clearInterval(this.staleCheckInterval); this.staleCheckInterval = null; }
    if (this.subscription) { this.subscription.close(); this.subscription = null; }
    this.resetState();
  }
}
