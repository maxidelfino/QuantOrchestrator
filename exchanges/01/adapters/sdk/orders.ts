import { FillMode, type NordUser, Side, type UserAtomicSubaction } from "@n1xyz/nord-ts";
import Decimal from "decimal.js";
import type { Quote } from "../../../../shared/typescript/types.js";
import { log } from "../../../../shared/typescript/utils/logger.js";

const MAX_ATOMIC_ACTIONS = 4;

export interface CachedOrder {
  orderId: string;
  side: "bid" | "ask";
  price: Decimal;
  size: Decimal;
}

interface AtomicResult {
  results: Array<{
    inner: {
      case: string;
      value: { orderId?: string; posted?: { orderId: string } };
    };
  }>;
}

function formatAction(action: UserAtomicSubaction): string {
  if (action.kind === "cancel") return `X${action.orderId}`;
  const side = action.side === Side.Bid ? "B" : "A";
  const fm =
    action.fillMode === FillMode.PostOnly ? "PO"
    : action.fillMode === FillMode.Limit ? "LIM"
    : action.fillMode === FillMode.ImmediateOrCancel ? "IOC"
    : "FOK";
  return `${side}[${fm}]@${action.price}x${action.size}`;
}

function extractPlacedOrders(result: AtomicResult, actions: UserAtomicSubaction[]): CachedOrder[] {
  const orders: CachedOrder[] = [];
  const placeActions = actions.filter((a) => a.kind === "place");
  let placeIdx = 0;
  for (const r of result.results) {
    if (r.inner.case === "placeOrderResult" && r.inner.value.posted?.orderId) {
      const action = placeActions[placeIdx];
      if (action && action.kind === "place") {
        orders.push({
          orderId: r.inner.value.posted.orderId,
          side: action.side === Side.Bid ? "bid" : "ask",
          price: new Decimal(action.price as Decimal.Value),
          size: new Decimal(action.size as Decimal.Value),
        });
      }
      placeIdx++;
    }
  }
  return orders;
}

async function executeAtomic(user: NordUser, actions: UserAtomicSubaction[]): Promise<CachedOrder[]> {
  if (actions.length === 0) return [];
  const allOrders: CachedOrder[] = [];
  const totalChunks = Math.ceil(actions.length / MAX_ATOMIC_ACTIONS);

  for (let i = 0; i < actions.length; i += MAX_ATOMIC_ACTIONS) {
    const chunkIdx = Math.floor(i / MAX_ATOMIC_ACTIONS) + 1;
    const chunk = actions.slice(i, i + MAX_ATOMIC_ACTIONS);
    log.info(`ATOMIC [${chunkIdx}/${totalChunks}]: ${chunk.map(formatAction).join(" ")}`);
    const result = (await user.atomic(chunk)) as AtomicResult;
    const placed = extractPlacedOrders(result, chunk);
    allOrders.push(...placed);
    if (placed.length > 0) {
      log.debug(`ATOMIC: placed [${placed.map((o) => o.orderId).join(", ")}]`);
    }
  }
  return allOrders;
}

function buildPlaceAction(marketId: number, quote: Quote): UserAtomicSubaction {
  return {
    kind: "place" as const,
    marketId,
    side: quote.side === "bid" ? Side.Bid : Side.Ask,
    fillMode: FillMode.PostOnly,  // PostOnly = never pays taker fee
    isReduceOnly: false,
    price: quote.price,
    size: quote.size,
  };
}

function buildCancelAction(orderId: string): UserAtomicSubaction {
  return { kind: "cancel" as const, orderId };
}

function orderMatchesQuote(order: CachedOrder, quote: Quote): boolean {
  return order.side === quote.side && order.price.eq(quote.price) && order.size.eq(quote.size);
}

// Update quotes: only cancel/place if changed — minimizes fees
export async function updateQuotes(
  user: NordUser,
  marketId: number,
  currentOrders: CachedOrder[],
  newQuotes: Quote[],
): Promise<CachedOrder[]> {
  const keptOrders: CachedOrder[] = [];
  const quotesToPlace: Quote[] = [];

  for (const quote of newQuotes) {
    const matchingOrder = currentOrders.find((o) => orderMatchesQuote(o, quote));
    if (matchingOrder) {
      keptOrders.push(matchingOrder);
    } else {
      quotesToPlace.push(quote);
    }
  }

  const ordersToCancel = currentOrders.filter((o) => !keptOrders.includes(o));

  if (ordersToCancel.length === 0 && quotesToPlace.length === 0) {
    return currentOrders;
  }

  const actions: UserAtomicSubaction[] = [
    ...ordersToCancel.map((o) => buildCancelAction(o.orderId)),
    ...quotesToPlace.map((q) => buildPlaceAction(marketId, q)),
  ];

  const placedOrders = await executeAtomic(user, actions);
  return [...keptOrders, ...placedOrders];
}

export async function cancelOrders(user: NordUser, orders: CachedOrder[]): Promise<void> {
  if (orders.length === 0) return;
  const actions = orders.map((o) => buildCancelAction(o.orderId));
  await executeAtomic(user, actions);
}
