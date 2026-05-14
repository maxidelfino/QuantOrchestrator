"""Allow running both bots: python -m bots.shared"""
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
from bots.btc_momentum_1h.strategy import BTCMomentum1hStrategy

load_dotenv()


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def cmd_status(config: BotConfig) -> None:
    """Show current bot status."""
    from bots.shared.state import StateManager

    state_mgr = StateManager(config.state_dir)
    state_mgr.connect()

    state = state_mgr.load()
    trades = state_mgr.fetch_trades(limit=10)
    events = state_mgr.fetch_events(limit=5)

    print("=" * 60)
    print("BOT STATUS")
    print("=" * 60)
    print(f"  Running:       {state.is_running}")
    print(f"  Last signal:   {state.last_signal}")
    print(f"  Errors:        {state.error_count}")
    if state.last_error:
        print(f"  Last error:    {state.last_error}")

    # Per-strategy positions
    print(f"\n  Positions:")
    if state.positions:
        for name, pos in state.positions.items():
            print(f"    [{name}] {pos['side']} {pos['quantity']:.6f} "
                  f"@ {pos['entry_price']} stop={pos['stop_price']}")
    else:
        print("    (none)")

    # Per-strategy last bar time
    if state.last_bar_time:
        print(f"\n  Last Bar Times:")
        for name, t in state.last_bar_time.items():
            print(f"    [{name}] {t}")

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
    parser = argparse.ArgumentParser(description="Multi-Strategy BTC Trading Bot")
    parser.add_argument("--status", action="store_true", help="Show bot status")
    parser.add_argument("--log-level", default="INFO", help="Logging level")
    args = parser.parse_args()

    setup_logging(args.log_level)

    # Discover bot directories
    bots_root = Path(__file__).resolve().parent.parent
    bot_dirs = sorted([d for d in bots_root.iterdir() if (d / "config.yaml").exists() and d.name != "shared"])

    if not bot_dirs:
        logging.error("No bot directories with config.yaml found under bots/")
        sys.exit(1)

    # Load configs for all bots
    all_configs = []
    for bot_dir in bot_dirs:
        try:
            cfg = BotConfig.from_yaml(str(bot_dir))
            all_configs.append(cfg)
        except Exception as e:
            logging.error(f"Failed to load {bot_dir.name}: {e}")
            sys.exit(1)

    if args.status:
        # Show status for the first bot's state dir
        cmd_status(all_configs[0])
        return

    # Validate all configs
    all_errors = []
    for cfg in all_configs:
        errors = cfg.validate()
        all_errors.extend(errors)
    if all_errors:
        logging.error("Configuration errors:")
        for e in all_errors:
            logging.error(f"  - {e}")
        sys.exit(1)

    # Build strategy engines from all loaded configs
    strategies = {}

    for cfg in all_configs:
        if cfg.btc_trend_4h and cfg.btc_trend_4h.enabled:
            engine = BTCTrend4hStrategy(
                ema_fast=cfg.btc_trend_4h.ema_fast,
                ema_slow=cfg.btc_trend_4h.ema_slow,
                ema_regime_daily=cfg.btc_trend_4h.ema_regime_daily,
                atr_period=cfg.btc_trend_4h.atr_period,
                stop_atr_mult=cfg.btc_trend_4h.stop_atr_mult,
            )
            strategies["btc-trend-4h"] = (engine, cfg.exchange.timeframe)

        if cfg.btc_momentum_1h and cfg.btc_momentum_1h.enabled:
            engine = BTCMomentum1hStrategy(
                rsi_period=cfg.btc_momentum_1h.rsi_period,
                adx_period=14,  # ADX period is fixed in the strategy
                adx_threshold=cfg.btc_momentum_1h.adx_threshold,
                rsi_long_min=cfg.btc_momentum_1h.rsi_long_min,
                rsi_long_max=cfg.btc_momentum_1h.rsi_long_max,
                rsi_short_min=cfg.btc_momentum_1h.rsi_short_min,
                rsi_short_max=cfg.btc_momentum_1h.rsi_short_max,
                atr_period=cfg.btc_momentum_1h.atr_period,
                stop_atr_mult=cfg.btc_momentum_1h.stop_atr_mult,
                max_hold_bars=cfg.btc_momentum_1h.max_hold,
                risk_pct=cfg.btc_momentum_1h.risk_pct,
            )
            strategies["btc-momentum-1h"] = (engine, cfg.exchange.timeframe)

    if not strategies:
        logging.error("No strategies enabled")
        sys.exit(1)

    # Use the first config as the primary config for the bot
    bot = TradingBot(all_configs[0], strategies)
    asyncio.run(bot.start())


if __name__ == "__main__":
    main()
