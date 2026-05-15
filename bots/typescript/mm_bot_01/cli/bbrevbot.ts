// Polyfill Uint8Array.prototype.toHex (not available in Node <v23)
declare global {
  interface Uint8Array {
    toHex(): string;
  }
}
if (!Uint8Array.prototype.toHex) {
  Uint8Array.prototype.toHex = function () {
    return Array.from(this)
      .map((b) => b.toString(16).padStart(2, "0"))
      .join("");
  };
}

import "dotenv/config";
import { DEFAULT_BBREV_CONFIG } from "../bots/bbrevbot/config.js";
import { BBRevBot } from "../bots/bbrevbot/index.js";
import { log } from "../../core/utils/logger.js";

function main(): void {
  const symbol = (process.argv[2] ?? "BTC").toUpperCase();

  const privateKey = process.env.PRIVATE_KEY;
  if (!privateKey) {
    console.error("Missing environment variable: PRIVATE_KEY");
    process.exit(1);
  }

  log.info(`Starting BB Mean Reversion Bot for ${symbol}-PERP...`);
  const bot = new BBRevBot({ symbol, ...DEFAULT_BBREV_CONFIG }, privateKey);

  bot.run().catch((err: unknown) => {
    log.error("Fatal error:", err);
    process.exit(1);
  });
}

main();
