#!/usr/bin/env python3
"""
v40 BTC Trend-Following Trading Bot — Entry Point

Usage:
    python -m bot.main              # Run bot
    python -m bot.main --dry-run    # Dry run (no orders)
    python -m bot.main --status     # Show current status
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from dotenv import load_dotenv

from bot.bot import TradingBot
from bot.config import BotConfig

load_dotenv()  # Load .env file before reading config


def setup_logging(level: str = "INFO") -> None:
    """Configure logging."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def cmd_run(config: BotConfig, dry_run: bool = False) -> None:
    """Run the trading bot."""
    logger = logging.getLogger("bot")
    if dry_run:
        logger.warning("DRY RUN MODE — No orders will be placed")
        # In dry run, we just log signals without executing
        config.exchange.testnet = True

    bot = TradingBot(config)
    asyncio.run(bot.start())


def cmd_status(config: BotConfig) -> None:
    """Show current bot status."""
    from bot.state import StateManager

    state_mgr = StateManager(config.state_dir)
    state_mgr.connect()

    state = state_mgr.load()
    trades = state_mgr.fetch_trades(limit=10)
    events = state_mgr.fetch_events(limit=5)

    print("=" * 60)
    print("BOT STATUS")
    print("=" * 60)
    print(f"  Running:       {state.is_running}")
    print(f"  Last bar:      {state.last_bar_time}")
    print(f"  Last signal:   {state.last_signal}")
    print(f"  Position:      {state.current_position}")
    print(f"  Errors:        {state.error_count}")
    if state.last_error:
        print(f"  Last error:    {state.last_error}")

    if trades:
        print(f"\n  Recent Trades ({len(trades)}):")
        for t in trades:
            print(f"    {t['side']} {t['symbol']} entry={t['entry_price']} "
                  f"exit={t['exit_price']} pnl={t['pnl']} reason={t['reason']}")

    if events:
        print(f"\n  Recent Events ({len(events)}):")
        for e in events:
            print(f"    [{e['event_type']}] {e['timestamp']}")

    state_mgr.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="v40 BTC Trend-Following Bot")
    parser.add_argument("--dry-run", action="store_true", help="Run without placing orders")
    parser.add_argument("--status", action="store_true", help="Show bot status")
    parser.add_argument("--log-level", default="INFO", help="Logging level")
    args = parser.parse_args()

    setup_logging(args.log_level)

    config = BotConfig.from_env()

    if args.status:
        cmd_status(config)
        return

    # Validate before running
    errors = config.validate()
    if errors:
        logging.error("Configuration errors:")
        for e in errors:
            logging.error(f"  - {e}")
        sys.exit(1)

    cmd_run(config, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
