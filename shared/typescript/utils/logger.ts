type LogOutput = (message: string) => void;
type LogLevel = "debug" | "info" | "warn" | "error";

// ANSI color codes for various log levels
const COLORS: Record<LogLevel, string> = {
  debug: "\x1b[36m", // cyan
  info: "\x1b[32m",  // green
  warn: "\x1b[33m",  // yellow
  error: "\x1b[31m", // red
};
const COLOR_RESET = "\x1b[0m";
const COLOR_BOLD = "\x1b[1m";

const LEVEL_PRIORITY: Record<LogLevel, number> = {
  debug: 0,
  info: 1,
  warn: 2,
  error: 3,
};

let outputFn: LogOutput = (msg) => console.log(msg);
let errorFn: LogOutput = (msg) => console.error(msg);
let minLevel: LogLevel = (process.env.LOG_LEVEL as LogLevel) || "info";

function timestamp(): string {
  return new Date().toISOString();
}

function formatArg(a: unknown): string {
  if (a instanceof Error) return a.stack || a.message;
  if (typeof a === "object") return JSON.stringify(a);
  return String(a);
}

function colorize(level: LogLevel, str: string): string {
  return COLORS[level] + str + COLOR_RESET;
}

function format(level: LogLevel, message: string, ...args: unknown[]): string {
  const argStr = args.length > 0 ? ` ${args.map(formatArg).join(" ")}` : "";
  // Make the [LEVEL] tag bold and colored
  const coloredLevel = colorize(
    level,
    `${COLOR_BOLD}[${level.toUpperCase()}]${COLOR_RESET}${COLORS[level]}`
  );
  return `${timestamp()} ${coloredLevel} ${message}${argStr}${COLOR_RESET}`;
}

function shouldLog(level: LogLevel): boolean {
  return LEVEL_PRIORITY[level] >= LEVEL_PRIORITY[minLevel];
}

export const log = {
  setOutput(fn: LogOutput): void {
    outputFn = fn;
    errorFn = fn;
  },
  setLevel(level: LogLevel): void {
    minLevel = level;
  },
  info(message: string, ...args: unknown[]): void {
    if (!shouldLog("info")) return;
    outputFn(format("info", message, ...args));
  },
  warn(message: string, ...args: unknown[]): void {
    if (!shouldLog("warn")) return;
    outputFn(format("warn", message, ...args));
  },
  error(message: string, ...args: unknown[]): void {
    if (!shouldLog("error")) return;
    errorFn(format("error", message, ...args));
  },
  debug(message: string, ...args: unknown[]): void {
    if (!shouldLog("debug")) return;
    outputFn(format("debug", message, ...args));
  },
  quote(
    bid: number | null,
    ask: number | null,
    fair: number,
    spreadBps: number,
    mode: "normal" | "close",
  ): void {
    const bidStr = bid !== null ? `$${bid.toFixed(2)}` : "--";
    const askStr = ask !== null ? `$${ask.toFixed(2)}` : "--";
    outputFn(
      format(
        "info",
        colorize("info",
          `QUOTE: BID ${bidStr} | ASK ${askStr} | FAIR $${fair.toFixed(2)} | SPREAD ${spreadBps}bps | ${mode.toUpperCase()}`
        ),
      ),
    );
  },
  position(
    sizeBase: number,
    sizeUsd: number,
    isLong: boolean,
    isCloseMode: boolean,
  ): void {
    const dir = isLong ? colorize("debug", "LONG") : colorize("warn", "SHORT");
    const mode = isCloseMode ? colorize("warn", " [CLOSE MODE]") : "";
    outputFn(
      format(
        "info",
        `POS: ${dir} ${Math.abs(sizeBase).toFixed(6)} ($${Math.abs(sizeUsd).toFixed(2)})${mode}`,
      ),
    );
  },
  fill(side: "buy" | "sell", price: number, size: number): void {
    const sideColored =
      side === "buy"
        ? colorize("debug", side.toUpperCase())
        : colorize("warn", side.toUpperCase());
    outputFn(
      format("info", `FILL: ${sideColored} ${size} @ $${price.toFixed(2)}`),
    );
  },
  banner(): void {
    // Banner in magenta/bold
    const bannerColor = "\x1b[35;1m";
    outputFn(
      `${bannerColor}
╔═══════════════════════════════════════╗
║         01 MARKET MAKER BOT           ║
╚═══════════════════════════════════════╝
${COLOR_RESET}`
    );
  },
  config(cfg: Record<string, unknown>): void {
    outputFn(format("info", colorize("info", "CONFIG:")));
    for (const [key, value] of Object.entries(cfg)) {
      outputFn(format("info", colorize("info", `  ${key}: ${value}`)));
    }
  },
  shutdown(): void {
    outputFn(format("info", colorize("info", "Shutting down...")));
  },
};