"""Template entrypoint. Run as: python -m exchanges.hyperliquid.bots.template"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

from shared.python.bot import TradingBot
from shared.python.config import BotConfig
from exchanges.hyperliquid.bots.template.strategy import TemplateStrategy

load_dotenv()


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Template Bot")
    parser.add_argument("--log-level", default="INFO", help="Logging level")
    args = parser.parse_args()

    setup_logging(args.log_level)

    # TODO: keep this config file next to this module after you copy/rename the bot folder.
    bot_dir = Path(__file__).resolve().parent
    config = BotConfig.from_yaml(str(bot_dir))

    errors = config.validate()
    if errors:
        logging.error("Configuration errors:")
        for e in errors:
            logging.error(f"  - {e}")
        sys.exit(1)

    # TODO: wire your strategy config section here.
    engine = TemplateStrategy()
    strategies = {
        # TODO: replace strategy key + timeframe with your bot values.
        "template-strategy": (engine, config.exchange.timeframe),
    }

    bot = TradingBot(config, strategies)
    asyncio.run(bot.start())


if __name__ == "__main__":
    main()
