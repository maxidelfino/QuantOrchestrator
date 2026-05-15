import Decimal from "decimal.js";
import type { BBO } from "../../../../../exchanges/typescript/zerone.js";
import type { Quote } from "../../../core/types.js";
import type { QuotingContext } from "./position.js";

export type { Quote } from "../../../core/types.js";

// 01 Exchange maker fee is ~0.02% (2 bps).
// A round trip (fill bid + fill ask) costs ~4 bps in fees.
// Our spread must exceed this to be profitable.
const MAKER_FEE_BPS = 2;
const MIN_PROFITABLE_SPREAD_BPS = MAKER_FEE_BPS * 2 + 1; // 5 bps minimum

export class Quoter {
  private readonly tickSize: Decimal;
  private readonly lotSize: Decimal;

  constructor(
    priceDecimals: number,
    sizeDecimals: number,
    private readonly spreadBps: number,
    private readonly takeProfitBps: number,
    private readonly orderSizeUsd: number,
    private readonly minSpreadBps: number = MIN_PROFITABLE_SPREAD_BPS,
  ) {
    this.tickSize = new Decimal(10).pow(-priceDecimals);
    this.lotSize = new Decimal(10).pow(-sizeDecimals);

    // Safety: refuse to start with a spread that can't cover fees
    if (spreadBps < MIN_PROFITABLE_SPREAD_BPS) {
      throw new Error(
        `spreadBps (${spreadBps}) is too tight. Minimum profitable spread is ${MIN_PROFITABLE_SPREAD_BPS} bps to cover maker fees.`,
      );
    }
  }

  getQuotes(ctx: QuotingContext, bbo: BBO | null): Quote[] {
    const { fairPrice, positionState, allowedSides } = ctx;
    const fair = new Decimal(fairPrice);

    // Use tighter spread in close mode (just enough to be profitable)
    const rawBps = positionState.isCloseMode
      ? this.takeProfitBps
      : this.spreadBps;

    // Enforce minimum spread — never quote below fee cost
    const bps = Math.max(rawBps, this.minSpreadBps);
    const spreadAmount = fair.mul(bps).div(10000);

    // In close mode: size = remaining position. Normal: fixed USD size.
    let size: Decimal;
    if (positionState.isCloseMode) {
      size = this.alignSize(new Decimal(positionState.sizeBase).abs());
    } else {
      size = this.usdToSize(this.orderSizeUsd, fair);
    }

    if (size.lte(0)) return [];

    const quotes: Quote[] = [];

    if (allowedSides.includes("bid")) {
      let bidPrice = this.alignPrice(fair.sub(spreadAmount), "floor");
      // Never cross the spread — bid must stay below best ask
      if (bbo && bidPrice.gte(bbo.bestAsk)) {
        bidPrice = this.alignPrice(
          new Decimal(bbo.bestAsk).sub(this.tickSize),
          "floor",
        );
      }
      if (bidPrice.gt(0)) quotes.push({ side: "bid", price: bidPrice, size });
    }

    if (allowedSides.includes("ask")) {
      let askPrice = this.alignPrice(fair.add(spreadAmount), "ceil");
      // Never cross the spread — ask must stay above best bid
      if (bbo && askPrice.lte(bbo.bestBid)) {
        askPrice = this.alignPrice(
          new Decimal(bbo.bestBid).add(this.tickSize),
          "ceil",
        );
      }
      if (askPrice.gt(0)) quotes.push({ side: "ask", price: askPrice, size });
    }

    return quotes;
  }

  private alignPrice(price: Decimal, round: "floor" | "ceil"): Decimal {
    const ticks = price.div(this.tickSize);
    const aligned = round === "floor" ? ticks.floor() : ticks.ceil();
    return aligned.mul(this.tickSize);
  }

  private usdToSize(usd: number, fairPrice: Decimal): Decimal {
    return this.alignSize(new Decimal(usd).div(fairPrice));
  }

  private alignSize(size: Decimal): Decimal {
    return size.div(this.lotSize).floor().mul(this.lotSize);
  }
}
