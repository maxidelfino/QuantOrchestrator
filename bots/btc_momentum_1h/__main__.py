"""Allow running btc-momentum-1h standalone: python -m bots.btc_momentum_1h"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

from bots.shared.config import BotConfig
from bots.shared.bot import TradingBot
from bots.btc_momentum_1h.strategy import BTCMomentum1hStrategy

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

    # Build strategy engine
    engine = BTCMomentum1hStrategy(
        rsi_period=config.btc_momentum_1h.rsi_period,
        adx_period=14,  # ADX period is fixed in the strategy
        adx_threshold=config.btc_momentum_1h.adx_threshold,
        rsi_long_min=config.btc_momentum_1h.rsi_long_min,
        rsi_long_max=config.btc_momentum_1h.rsi_long_max,
        rsi_short_min=config.btc_momentum_1h.rsi_short_min,
        rsi_short_max=config.btc_momentum_1h.rsi_short_max,
        atr_period=config.btc_momentum_1h.atr_period,
        stop_atr_mult=config.btc_momentum_1h.stop_atr_mult,
        max_hold_bars=config.btc_momentum_1h.max_hold,
        risk_pct=config.btc_momentum_1h.risk_pct,
    )

    strategies = {
        "btc-momentum-1h": (engine, config.exchange.timeframe),
    }

    bot = TradingBot(config, strategies)
    asyncio.run(bot.start())


if __name__ == "__main__":
    main()
