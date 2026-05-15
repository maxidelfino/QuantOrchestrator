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
import { DEFAULT_SQZ_CONFIG } from "../bots/sqzbot/config.js";
import { SQZBot } from "../bots/sqzbot/index.js";
import { log } from "../../../../../shared/typescript/utils/logger.js";

function main(): void {
  // Default to HYPE (best backtested pair for squeeze strategy)
  const symbol = (process.argv[2] ?? "HYPE").toUpperCase();

  const privateKey = process.env.PRIVATE_KEY;
  if (!privateKey) {
    console.error("Missing environment variable: PRIVATE_KEY");
    process.exit(1);
  }

  log.info(`Starting Squeeze Momentum Bot for ${symbol}-PERP...`);
  const bot = new SQZBot({ symbol, ...DEFAULT_SQZ_CONFIG }, privateKey);

  bot.run().catch((err: unknown) => {
    log.error("Fatal error:", err);
    process.exit(1);
  });
}

main();
