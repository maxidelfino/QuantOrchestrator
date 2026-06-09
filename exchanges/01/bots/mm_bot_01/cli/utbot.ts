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
import { DEFAULT_UTBOT_CONFIG } from "../bots/utbot/config.js";
import { UTBot } from "../bots/utbot/index.js";
import { log } from "../../../../../shared/typescript/utils/logger.js";

function main(): void {
  // Symbol defaults to BTC if not provided
  const symbol = (process.argv[2] ?? "BTC").toUpperCase();

  const privateKey = process.env.PRIVATE_KEY;
  if (!privateKey) {
    console.error("Missing environment variable: PRIVATE_KEY");
    process.exit(1);
  }

  log.info(`Starting Donchian Breakout Bot for ${symbol}-PERP...`);
  const bot = new UTBot({ symbol, ...DEFAULT_UTBOT_CONFIG }, privateKey);

  bot.run().catch((err: unknown) => {
    log.error("Fatal error:", err);
    process.exit(1);
  });
}

main();
