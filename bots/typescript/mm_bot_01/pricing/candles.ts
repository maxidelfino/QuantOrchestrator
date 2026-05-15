import WebSocket from "ws";
import { log } from "../../core/utils/logger.js";

const BINANCE_FUTURES_WS = "wss://fstream.binance.com/ws";
const PING_INTERVAL_MS = 30_000;
const PONG_TIMEOUT_MS = 10_000;
// Candles close every 60s, so 120s stale threshold is safe
const STALE_THRESHOLD_MS = 120_000;
const STALE_CHECK_INTERVAL_MS = 30_000;
const RECONNECT_DELAY_MS = 3_000;

export interface Candle {
  time: number;   // Open time in ms
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

type CandleCallback = (candle: Candle) => void;

export class BinanceCandleFeed {
  private ws: WebSocket | null = null;
  private candles: Candle[] = [];
  private reconnectTimeout: NodeJS.Timeout | null = null;
  private pingInterval: NodeJS.Timeout | null = null;
  private pongTimeout: NodeJS.Timeout | null = null;
  private staleCheckInterval: NodeJS.Timeout | null = null;
  private lastMessageTime = 0;
  private isClosing = false;
  private historyLoaded = false;
  private readonly wsUrl: string;
  private readonly restUrl: string;

  onCandle: CandleCallback | null = null;

  constructor(
    private readonly symbol: string = "btcusdt",
    private readonly historyLength: number = 100,
  ) {
    const sym = symbol.toLowerCase();
    this.wsUrl = `${BINANCE_FUTURES_WS}/${sym}@kline_1m`;
    // REST uses UPPERCASE symbol
    this.restUrl = `https://fapi.binance.com/fapi/v1/klines?symbol=${sym.toUpperCase()}&interval=1m&limit=${historyLength}`;
  }

  getCandles(): readonly Candle[] {
    return this.candles;
  }

  /** Fetch historical candles from Binance Futures REST API */
  async loadHistory(): Promise<void> {
    log.info(`Loading ${this.historyLength} candles from Binance Futures REST...`);

    const response = await fetch(this.restUrl);
    if (!response.ok) {
      throw new Error(`Binance REST error: ${response.status} ${response.statusText}`);
    }

    type KlineRow = [number, string, string, string, string, string, ...unknown[]];
    const data = (await response.json()) as KlineRow[];

    this.candles = data.map(([time, open, high, low, close, volume]) => ({
      time: Number(time),
      open: parseFloat(open),
      high: parseFloat(high),
      low: parseFloat(low),
      close: parseFloat(close),
      volume: parseFloat(volume),
    }));

    this.historyLoaded = true;
    const last = this.candles[this.candles.length - 1];
    log.info(`Loaded ${this.candles.length} historical candles. Last close: $${last?.close.toFixed(2)}`);
  }

  connect(): void {
    if (this.ws) return;
    log.info(`Connecting to Binance kline stream (1m)...`);
    this.ws = new WebSocket(this.wsUrl);

    this.ws.on("open", () => {
      log.info("Binance kline stream connected");
      this.lastMessageTime = Date.now();
      this.startPingInterval();
      this.startStaleCheck();
    });

    this.ws.on("message", (data: Buffer) => {
      this.lastMessageTime = Date.now();
      try {
        const msg = JSON.parse(data.toString()) as {
          k: {
            t: number;   // kline open time
            o: string;   // open
            h: string;   // high
            l: string;   // low
            c: string;   // close
            v: string;   // volume
            x: boolean;  // is this kline closed?
          };
        };

        if (msg.k.x) {
          // Only emit on CLOSED candles
          const candle: Candle = {
            time: msg.k.t,
            open: parseFloat(msg.k.o),
            high: parseFloat(msg.k.h),
            low: parseFloat(msg.k.l),
            close: parseFloat(msg.k.c),
            volume: parseFloat(msg.k.v),
          };

          if (this.historyLoaded) {
            const last = this.candles[this.candles.length - 1];
            if (!last || last.time < candle.time) {
              this.candles.push(candle);
              // Keep buffer from growing unbounded
              if (this.candles.length > this.historyLength * 2) {
                this.candles = this.candles.slice(-this.historyLength);
              }
            }
          }

          if (this.onCandle) this.onCandle(candle);
        }
      } catch {
        // Ignore parse errors
      }
    });

    this.ws.on("ping", (data: Buffer) => { this.ws?.pong(data); });
    this.ws.on("pong", () => { this.clearPongTimeout(); });
    this.ws.on("error", (err: Error) => { log.error("Binance kline WS error:", err.message); });
    this.ws.on("close", () => {
      log.warn("Binance kline stream disconnected");
      this.cleanup();
      if (!this.isClosing) this.scheduleReconnect();
    });
  }

  private startPingInterval(): void {
    this.stopPingInterval();
    this.pingInterval = setInterval(() => {
      if (this.ws?.readyState === WebSocket.OPEN) {
        this.ws.ping();
        this.startPongTimeout();
      }
    }, PING_INTERVAL_MS);
  }

  private stopPingInterval(): void {
    if (this.pingInterval) { clearInterval(this.pingInterval); this.pingInterval = null; }
  }

  private startPongTimeout(): void {
    this.clearPongTimeout();
    this.pongTimeout = setTimeout(() => {
      log.warn("Binance kline pong timeout - reconnecting");
      this.ws?.terminate();
    }, PONG_TIMEOUT_MS);
  }

  private clearPongTimeout(): void {
    if (this.pongTimeout) { clearTimeout(this.pongTimeout); this.pongTimeout = null; }
  }

  private startStaleCheck(): void {
    this.stopStaleCheck();
    this.staleCheckInterval = setInterval(() => {
      if (this.isClosing) return;
      const timeSinceMessage = Date.now() - this.lastMessageTime;
      if (this.lastMessageTime > 0 && timeSinceMessage > STALE_THRESHOLD_MS) {
        log.warn(`Binance kline stale (${timeSinceMessage}ms). Reconnecting...`);
        this.ws?.terminate();
      }
    }, STALE_CHECK_INTERVAL_MS);
  }

  private stopStaleCheck(): void {
    if (this.staleCheckInterval) { clearInterval(this.staleCheckInterval); this.staleCheckInterval = null; }
  }

  private cleanup(): void {
    this.stopPingInterval();
    this.clearPongTimeout();
    this.stopStaleCheck();
    this.ws = null;
  }

  private scheduleReconnect(): void {
    if (this.reconnectTimeout) return;
    log.info(`Reconnecting to Binance kline stream in ${RECONNECT_DELAY_MS}ms...`);
    this.reconnectTimeout = setTimeout(() => {
      this.reconnectTimeout = null;
      this.connect();
    }, RECONNECT_DELAY_MS);
  }

  close(): void {
    this.isClosing = true;
    if (this.reconnectTimeout) { clearTimeout(this.reconnectTimeout); this.reconnectTimeout = null; }
    this.cleanup();
    if (this.ws) { this.ws.close(); this.ws = null; }
  }
}
