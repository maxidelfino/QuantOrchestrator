"""
Bot Configuration — v40 BTC Trend-Following

Loads from environment variables with sensible defaults.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class StrategyConfig:
    """v40 strategy parameters."""
    ema_fast: int = 50
    ema_slow: int = 200
    ema_regime_daily: int = 200
    atr_period: int = 14
    stop_atr_mult: float = 3.0
    risk_pct: float = 0.02
    warmup_bars: int = 220


@dataclass
class ExchangeConfig:
    """Binance Futures connection."""
    api_key: str = ""
    api_secret: str = ""
    symbol: str = "BTCUSDT"
    timeframe: str = "4h"
    testnet: bool = True  # Default to testnet for safety
    leverage: int = 1  # Cross margin, 1x by default


@dataclass
class RiskConfig:
    """Risk management limits."""
    max_position_pct: float = 0.10  # Max 10% of equity in one position
    max_daily_loss_pct: float = 0.05  # Stop trading after 5% daily loss
    max_drawdown_pct: float = 0.25  # Kill switch at 25% drawdown
    max_open_orders: int = 5
    min_order_size_usd: float = 5.0  # Minimum order in USD


@dataclass
class BotConfig:
    """Full bot configuration."""
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    exchange: ExchangeConfig = field(default_factory=ExchangeConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    state_dir: str = "bot/state"
    log_level: str = "INFO"
    poll_interval_sec: int = 60  # Check every 60s (4h bars)

    @classmethod
    def from_env(cls) -> "BotConfig":
        """Load configuration from environment variables."""
        return cls(
            strategy=StrategyConfig(
                risk_pct=float(os.getenv("BOT_RISK_PCT", "0.02")),
                stop_atr_mult=float(os.getenv("BOT_STOP_ATR_MULT", "3.0")),
            ),
            exchange=ExchangeConfig(
                api_key=os.getenv("BINANCE_API_KEY", ""),
                api_secret=os.getenv("BINANCE_API_SECRET", ""),
                symbol=os.getenv("BOT_SYMBOL", "BTCUSDT"),
                testnet=os.getenv("BOT_TESTNET", "true").lower() == "true",
                leverage=int(os.getenv("BOT_LEVERAGE", "1")),
            ),
            risk=RiskConfig(
                max_daily_loss_pct=float(os.getenv("BOT_MAX_DAILY_LOSS", "0.05")),
                max_drawdown_pct=float(os.getenv("BOT_MAX_DRAWDOWN", "0.25")),
            ),
            log_level=os.getenv("BOT_LOG_LEVEL", "INFO"),
            poll_interval_sec=int(os.getenv("BOT_POLL_INTERVAL", "60")),
        )

    def validate(self) -> list[str]:
        """Return list of validation errors (empty = OK)."""
        errors = []
        if not self.exchange.api_key:
            errors.append("BINANCE_API_KEY not set")
        if not self.exchange.api_secret:
            errors.append("BINANCE_API_SECRET not set")
        if self.strategy.risk_pct <= 0 or self.strategy.risk_pct > 0.10:
            errors.append("risk_pct must be between 0 and 0.10")
        if self.risk.max_drawdown_pct <= 0 or self.risk.max_drawdown_pct > 0.50:
            errors.append("max_drawdown_pct must be between 0 and 0.50")
        return errors
