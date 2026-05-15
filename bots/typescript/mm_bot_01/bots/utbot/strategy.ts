import { FillMode, type NordUser, Side } from "@n1xyz/nord-ts";
import Decimal from "decimal.js";
import { log } from "../../../core/utils/logger.js";

export type OrderDirection = "buy" | "sell";

/**
 * Place an IOC (Immediate-Or-Cancel) order — market-like execution.
 *
 * For BUY:  Side.Bid at askRef * (1 + slippage) — aggressive buy limit
 * For SELL: Side.Ask at bidRef * (1 - slippage) — aggressive sell limit
 *
 * The price is set well above/below market so the IOC will fill immediately
 * against resting orders. Any unfilled portion is cancelled automatically.
 *
 * Returns true if the order was accepted by the exchange (submitted without error).
 * Use verifyPosition() afterwards to confirm the actual fill.
 */
export async function placeIOCOrder(
  user: NordUser,
  marketId: number,
  direction: OrderDirection,
  priceRef: number,
  size: Decimal,
  priceDecimals: number,
  sizeDecimals: number,
  slippageBps: number,
  reduceOnly: boolean,
): Promise<boolean> {
  const slippage = slippageBps / 10_000;
  const rawPrice = direction === "buy"
    ? priceRef * (1 + slippage)
    : priceRef * (1 - slippage);

  const price = new Decimal(rawPrice).toDecimalPlaces(priceDecimals, Decimal.ROUND_HALF_UP);
  const roundedSize = size.toDecimalPlaces(sizeDecimals, Decimal.ROUND_DOWN);

  if (roundedSize.lte(0)) {
    log.warn("IOC order skipped: size rounds to zero");
    return false;
  }

  const side = direction === "buy" ? Side.Bid : Side.Ask;
  const tag = `${direction === "buy" ? "BID" : "ASK"}${reduceOnly ? " [CLOSE]" : " [OPEN]"}`;

  log.info(`Placing IOC ${tag} @ $${price.toFixed(priceDecimals)} x ${roundedSize}`);

  try {
    await user.atomic([{
      kind: "place" as const,
      marketId,
      side,
      fillMode: FillMode.ImmediateOrCancel,
      isReduceOnly: reduceOnly,
      price,
      size: roundedSize,
    }]);
    log.info(`IOC ${tag} submitted`);
    return true;
  } catch (err) {
    log.error(`IOC ${tag} failed:`, err);
    return false;
  }
}

/**
 * Calculate position size in base currency (BTC).
 * Formula: (balance * riskPercent% * leverage) / price
 */
export function calcPositionSize(
  balanceUsdc: number,
  riskPercent: number,
  leverage: number,
  price: number,
  sizeDecimals: number,
): Decimal {
  const margin = balanceUsdc * (riskPercent / 100);
  const notional = margin * leverage;
  const size = notional / price;
  return new Decimal(size).toDecimalPlaces(sizeDecimals, Decimal.ROUND_DOWN);
}

/** Max leverage per asset: BTC/ETH=50x, SOL/HYPE=20x, others=50x */
export function getMaxLeverage(symbol: string): number {
  const s = symbol.toUpperCase().replace(/-PERP$/i, "");
  if (s === "SOL" || s === "HYPE") return 20;
  return 50;
}

/**
 * Calculate position size based on actual risk (SL distance).
 * This ensures that if SL is hit, you lose exactly riskPct of balance.
 *
 * Formula: size = (balance × riskPct) / slDistance
 *   where slDistance is the price distance to SL (e.g., 1.5 × ATR).
 *
 * The leverage is implicit: notional / balance.
 * maxLeverage caps the position (default 50x).
 */
export function calcRiskBasedSize(
  balanceUsdc: number,
  riskPct: number,
  slDistance: number,
  price: number,
  sizeDecimals: number,
  maxLeverage = 50,
): Decimal {
  if (slDistance <= 0 || price <= 0) return new Decimal(0);

  const riskAmount = balanceUsdc * riskPct;
  const sizeBase = riskAmount / slDistance;

  // Cap notional at maxLeverage × balance
  const notional = sizeBase * price;
  const maxNotional = balanceUsdc * maxLeverage;
  const finalSize = notional > maxNotional ? (maxNotional / price) : sizeBase;

  return new Decimal(finalSize).toDecimalPlaces(sizeDecimals, Decimal.ROUND_DOWN);
}

/** Get USDC balance for an account */
export async function getBalance(user: NordUser, accountId: number): Promise<number> {
  await user.fetchInfo();
  const balances = user.balances[accountId] ?? [];
  const usdc = balances.find((b) => b.symbol === "USDC");
  return usdc?.balance ?? 0;
}

export interface ServerPosition {
  size: number;      // base asset size (always positive)
  isLong: boolean;
}

/** Fetch current position from the exchange server */
export async function getServerPosition(
  user: NordUser,
  accountId: number,
  marketId: number,
): Promise<ServerPosition> {
  await user.fetchInfo();
  const positions = user.positions[accountId] ?? [];
  const pos = positions.find((p) => p.marketId === marketId);
  if (!pos?.perp || pos.perp.baseSize === 0) return { size: 0, isLong: true };
  return { size: pos.perp.baseSize, isLong: pos.perp.isLong };
}
