"""Allow running btc-trend-4h standalone: python -m bots.btc_trend_4h"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

from bots.shared.config import BotConfig
from bots.shared.bot import TradingBot
from bots.btc_trend_4h.strategy import BTCTrend4hStrategy

load_dotenv()


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="BTC Trend-Following 4h")
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

    # Build strategy engine
    engine = BTCTrend4hStrategy(
        ema_fast=config.btc_trend_4h.ema_fast,
        ema_slow=config.btc_trend_4h.ema_slow,
        ema_regime_daily=config.btc_trend_4h.ema_regime_daily,
        atr_period=config.btc_trend_4h.atr_period,
        stop_atr_mult=config.btc_trend_4h.stop_atr_mult,
    )

    strategies = {
        "btc-trend-4h": (engine, config.exchange.timeframe),
    }

    bot = TradingBot(config, strategies)
    asyncio.run(bot.start())


if __name__ == "__main__":
    main()
