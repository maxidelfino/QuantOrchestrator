/**
 * Historical candle data fetcher from Binance Futures REST API.
 * Supports pagination (max 1500 per request) and local JSON caching.
 */

import { existsSync, mkdirSync, readFileSync, writeFileSync } from "fs";
import { join } from "path";

export interface Candle {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

const BINANCE_KLINES_URL = "https://fapi.binance.com/fapi/v1/klines";
const MAX_PER_REQUEST = 1500;
const ONE_MINUTE_MS = 60_000;
const CACHE_DIR = join(process.cwd(), ".cache", "candles");

function symbolToBinance(symbol: string): string {
  return `${symbol.toUpperCase()}USDT`;
}

function getCachePath(symbol: string, days: number): string {
  return join(CACHE_DIR, `${symbol.toLowerCase()}_1m_${days}d.json`);
}

/** Check if cache is fresh (less than 1 hour old) */
function isCacheFresh(path: string): boolean {
  try {
    const { mtimeMs } = require("fs").statSync(path);
    return Date.now() - mtimeMs < 3600_000;
  } catch {
    return false;
  }
}

async function fetchKlines(
  binanceSymbol: string,
  startTime: number,
  endTime: number,
): Promise<Candle[]> {
  const url = new URL(BINANCE_KLINES_URL);
  url.searchParams.set("symbol", binanceSymbol);
  url.searchParams.set("interval", "1m");
  url.searchParams.set("startTime", startTime.toString());
  url.searchParams.set("endTime", endTime.toString());
  url.searchParams.set("limit", MAX_PER_REQUEST.toString());

  const response = await fetch(url.toString());
  if (!response.ok) {
    throw new Error(`Binance API error: ${response.status} ${response.statusText}`);
  }

  type KlineRow = [number, string, string, string, string, string, ...unknown[]];
  const data = (await response.json()) as KlineRow[];

  return data.map(([time, open, high, low, close, volume]) => ({
    time: Number(time),
    open: parseFloat(open),
    high: parseFloat(high),
    low: parseFloat(low),
    close: parseFloat(close),
    volume: parseFloat(volume),
  }));
}

/**
 * Fetch historical 1m candles from Binance Futures.
 * Paginates automatically for periods > 1500 candles.
 * Caches results to local JSON files.
 */
export async function fetchCandles(
  symbol: string,
  days: number,
): Promise<Candle[]> {
  const cachePath = getCachePath(symbol, days);

  // Try cache first
  if (existsSync(cachePath) && isCacheFresh(cachePath)) {
    console.log(`Loading cached candles from ${cachePath}`);
    const cached = JSON.parse(readFileSync(cachePath, "utf-8")) as Candle[];
    console.log(`Loaded ${cached.length} candles from cache`);
    return cached;
  }

  const binanceSymbol = symbolToBinance(symbol);
  const endTime = Date.now();
  const startTime = endTime - days * 24 * 60 * 60 * 1000;
  const totalCandles = days * 24 * 60; // 1m candles per day = 1440

  console.log(`Fetching ${totalCandles} candles for ${binanceSymbol} (${days} days)...`);

  const allCandles: Candle[] = [];
  let cursor = startTime;

  while (cursor < endTime) {
    const batchEnd = Math.min(cursor + MAX_PER_REQUEST * ONE_MINUTE_MS, endTime);
    const batch = await fetchKlines(binanceSymbol, cursor, batchEnd);

    if (batch.length === 0) break;

    allCandles.push(...batch);
    cursor = batch[batch.length - 1].time + ONE_MINUTE_MS;

    const pct = Math.min(100, ((cursor - startTime) / (endTime - startTime)) * 100);
    process.stdout.write(`\r  Progress: ${pct.toFixed(0)}% (${allCandles.length} candles)`);

    // Rate limit: small delay between requests
    if (cursor < endTime) {
      await new Promise((r) => setTimeout(r, 200));
    }
  }

  console.log(`\nFetched ${allCandles.length} candles total`);

  // Deduplicate by time
  const seen = new Set<number>();
  const deduped = allCandles.filter((c) => {
    if (seen.has(c.time)) return false;
    seen.add(c.time);
    return true;
  });

  // Sort by time
  deduped.sort((a, b) => a.time - b.time);

  // Cache
  mkdirSync(CACHE_DIR, { recursive: true });
  writeFileSync(cachePath, JSON.stringify(deduped));
  console.log(`Cached ${deduped.length} candles to ${cachePath}`);

  return deduped;
}

/**
 * Aggregate 1m candles into higher timeframes (5m, 15m, etc).
 * Each output candle represents `minutes` consecutive 1m candles.
 */
export function aggregateCandles(candles1m: Candle[], minutes: number): Candle[] {
  if (minutes <= 1) return candles1m;
  const result: Candle[] = [];
  const intervalMs = minutes * 60_000;

  for (const c of candles1m) {
    const bucketTime = c.time - (c.time % intervalMs);
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

  return result;
}
