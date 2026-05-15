// Polyfill Uint8Array.prototype.toHex (not available in Node <v23)
/*
// Para ejecutar el bot
// tmux new-session -d -s utbot 'cd /Users/maxidelfino/Desktop/bots/mm_bot_01 && npm run utbot:btc'
// tmux new-session -d -s bbrevbot 'cd /Users/maxidelfino/Desktop/bots/mm_bot_01 && npm run bbrevbot:eth'
// tmux new-session -d -s sqzbot 'cd /Users/maxidelfino/Desktop/bots/mm_bot_01 && npm run sqzbot:sol'
//
// Para ver los logs
// tmux attach -t utbot
// tmux attach -t bbrevbot
// tmux attach -t sqzbot

*/
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
import { DEFAULT_CONFIG } from "../bots/mm/config.js";
import { MarketMaker } from "../bots/mm/index.js";
import { log } from "../../../../../shared/typescript/utils/logger.js";

function main(): void {
  const symbol = process.argv[2]?.toUpperCase();
  if (!symbol) {
    console.error("Usage: npm run bot -- <symbol>");
    console.error("Example: npm run bot -- BTC");
    console.error("Example: npm run bot -- ETH");
    process.exit(1);
  }

  const privateKey = process.env.PRIVATE_KEY;
  if (!privateKey) {
    console.error("Missing required environment variable: PRIVATE_KEY");
    process.exit(1);
  }

  const bot = new MarketMaker({ symbol, ...DEFAULT_CONFIG }, privateKey);

  bot.run().catch((err: unknown) => {
    log.error("Fatal error:", err);
    process.exit(1);
  });
}

main();
