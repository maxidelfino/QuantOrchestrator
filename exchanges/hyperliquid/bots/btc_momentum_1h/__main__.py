"""Allow running btc-momentum-1h standalone: python -m exchanges.hyperliquid.bots.btc_momentum_1h"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

from shared.python.config import BotConfig
from shared.python.bot import TradingBot

load_dotenv()


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="BTC Momentum 1h")
    parser.add_argument("--log-level", default="INFO", help="Logging level")
    args = parser.parse_args()

    setup_logging(args.log_level)

    # Load config from this bot's config.yaml
    bot_dir = Path(__file__).resolve().parent
    config = BotConfig.from_yaml(str(bot_dir))

    errors = config.validate()
    if errors:
        logging.error("Configuration errors:")
        for e in errors:
            logging.error(f"  - {e}")
        sys.exit(1)

    # Build strategy engine (dynamic from config.strategy.strategy_class)
    engine = config.create_strategy_engine()

    strategies = {
        "btc-momentum-1h": (engine, config.exchange.timeframe),
    }

    bot = TradingBot(config, strategies)
    asyncio.run(bot.start())


if __name__ == "__main__":
    main()
