"""
Bot Configuration — Multi-strategy

Loading model:
- .env → SECRETS ONLY (API keys, wallet addresses, private keys)
- config.yaml → ALL bot-specific params (exchange, strategy, risk)

Environment variables DO NOT override YAML defaults for non-secret params.
Each bot loads its own config.yaml; secrets are shared from .env.

Usage:
    # Load a specific bot's config
    config = BotConfig.from_yaml("bots/btc_trend_4h")

    # Load with explicit path
    config = BotConfig.from_yaml("/path/to/bot_dir")
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

import yaml


# ---------------------------------------------------------------------------
# Strategy configs
# ---------------------------------------------------------------------------

@dataclass
class BTCTrend4hConfig:
    """btc-trend-4h (formerly v40) — 4h EMA trend-following with daily regime filter.

    Parameters mirror what BTCTrend4hStrategy accepts.
    """
    ema_fast: int = 50
    ema_slow: int = 200
    ema_regime_daily: int = 200
    atr_period: int = 14
    stop_atr_mult: float = 3.0
    risk_pct: float = 0.02
    enabled: bool = True


@dataclass
class BTCMomentum1hConfig:
    """btc-momentum-1h (formerly v48b) — 1h RSI momentum pullback with ADX filter.

    Parameters mirror what BTCMomentum1hStrategy accepts.
    """
    rsi_period: int = 14
    adx_threshold: float = 20.0
    rsi_long_min: float = 35.0
    rsi_long_max: float = 50.0
    rsi_short_min: float = 50.0
    rsi_short_max: float = 65.0
    atr_period: int = 14
    stop_atr_mult: float = 3.0
    max_hold: int = 16
    risk_pct: float = 0.02
    enabled: bool = True


# ---------------------------------------------------------------------------
# Exchange / Venue config
# ---------------------------------------------------------------------------

@dataclass
class ExchangeConfig:
    """Exchange connection parameters.

    Secrets (api_key, private_key, wallet_address) come from .env.
    All other fields come from config.yaml.
    """
    venue: str = "hyperliquid"        # Venue identifier
    api_key: str = ""                 # From .env (BINANCE_API_KEY)
    api_secret: str = ""              # From .env (BINANCE_API_SECRET)
    wallet_address: str = ""          # From .env (HYPERLIQUID_WALLET_ADDRESS)
    private_key: str = ""             # From .env (HYPERLIQUID_PRIVATE_KEY)
    symbol: str = "BTC/USDC:USDC"    # From config.yaml
    timeframe: str = "1h"             # From config.yaml
    testnet: bool = True              # From config.yaml
    leverage: int = 1                 # From config.yaml


# Venue lifecycle
SUPPORTED_VENUES = {"hyperliquid", "binance"}
PLANNED_VENUES = {"extended", "01", "risex", "paradex", "lighter"}


# ---------------------------------------------------------------------------
# Risk config
# ---------------------------------------------------------------------------

@dataclass
class RiskConfig:
    """Risk management limits (circuit breakers)."""
    max_daily_loss_pct: float = 0.05     # Stop trading after 5% daily loss
    max_drawdown_pct: float = 0.25       # Kill switch at 25% drawdown
    min_order_size_usd: float = 5.0      # Minimum order in USD
    max_position_pct: float = 0.20       # Maximum position value as % of equity


# ---------------------------------------------------------------------------
# Execution config
# ---------------------------------------------------------------------------

@dataclass
class ExecutionConfig:
    """Order execution parameters.

    Controls entry/exit order types, TTL for limit orders, and price offsets.
    """
    entry_order_type: str = "market"       # "limit" or "market"
    entry_ttl_bars: int = 3                # Cancel limit after N bars if not filled
    exit_stop_type: str = "market"         # Always market for stop-loss exits
    exit_planned_type: str = "market"      # "limit" or "market" for planned exits
    limit_price_offset: float = 0.0001     # 0.01% better than close for limit placement
    post_only: bool = True                 # postOnly flag for maker-only limit orders


# ---------------------------------------------------------------------------
# Root bot config
# ---------------------------------------------------------------------------

@dataclass
class BotConfig:
    """Full bot configuration for a single strategy instance.

    Each bot (btc_trend_4h, btc_momentum_1h, etc.) has its own BotConfig
    loaded from its config.yaml + shared secrets from .env.
    """
    exchange: ExchangeConfig = field(default_factory=ExchangeConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    state_dir: str = "bot/state"
    log_level: str = "INFO"

    # Strategy-specific configs (only the one matching this bot is populated)
    btc_trend_4h: Optional[BTCTrend4hConfig] = None
    btc_momentum_1h: Optional[BTCMomentum1hConfig] = None

    @classmethod
    def from_yaml(cls, bot_dir: str) -> "BotConfig":
        """Load bot configuration from YAML + environment secrets.

        Args:
            bot_dir: Path to the bot directory (e.g. "bots/btc_trend_4h"
                     or "bots/btc_momentum_1h"). Must contain config.yaml.

        Returns:
            BotConfig with secrets from .env and params from config.yaml.
        """
        bot_path = Path(bot_dir)
        config_file = bot_path / "config.yaml"

        if not config_file.exists():
            raise FileNotFoundError(
                f"Config file not found: {config_file}. "
                f"Each bot must have a config.yaml in its directory."
            )

        with open(config_file) as f:
            data = yaml.safe_load(f) or {}

        bot_name = bot_path.name
        return cls._from_dict(data, bot_name)

    @classmethod
    def _from_dict(cls, data: Dict[str, Any], bot_name: str) -> "BotConfig":
        """Build BotConfig from parsed YAML dict + env secrets."""

        # --- Exchange: merge YAML params with env secrets ---
        ex = data.get("exchange", {})
        exchange = ExchangeConfig(
            venue=ex.get("venue", "hyperliquid"),
            api_key=os.getenv("BINANCE_API_KEY", ""),
            api_secret=os.getenv("BINANCE_API_SECRET", ""),
            wallet_address=os.getenv("HYPERLIQUID_WALLET_ADDRESS", ""),
            private_key=os.getenv("HYPERLIQUID_PRIVATE_KEY", ""),
            symbol=ex.get("symbol", "BTC/USDC:USDC"),
            timeframe=ex.get("timeframe", "1h"),
            testnet=ex.get("testnet", True),
            leverage=int(ex.get("leverage", 1)),
        )

        # --- Execution: from YAML only ---
        exec_data = data.get("execution", {})
        execution = ExecutionConfig(
            entry_order_type=exec_data.get("entry_order_type", "market"),
            entry_ttl_bars=int(exec_data.get("entry_ttl_bars", 3)),
            exit_stop_type=exec_data.get("exit_stop_type", "market"),
            exit_planned_type=exec_data.get("exit_planned_type", "market"),
            limit_price_offset=float(exec_data.get("limit_price_offset", 0.0001)),
            post_only=bool(exec_data.get("post_only", True)),
        )

        # --- Risk: from YAML only ---
        risk_data = data.get("risk", {})
        risk = RiskConfig(
            max_daily_loss_pct=float(risk_data.get("max_daily_loss_pct", 0.05)),
            max_drawdown_pct=float(risk_data.get("max_drawdown_pct", 0.25)),
            min_order_size_usd=float(risk_data.get("min_order_size_usd", 5.0)),
            max_position_pct=float(risk_data.get("max_position_pct", 0.20)),
        )

        # --- Strategy: populate based on bot_name ---
        strat_data = data.get("strategy", {})
        trend_cfg: Optional[BTCTrend4hConfig] = None
        momentum_cfg: Optional[BTCMomentum1hConfig] = None

        if bot_name == "btc_trend_4h":
            trend_cfg = BTCTrend4hConfig(
                ema_fast=int(strat_data.get("ema_fast", 50)),
                ema_slow=int(strat_data.get("ema_slow", 200)),
                ema_regime_daily=int(strat_data.get("ema_regime_daily", 200)),
                atr_period=int(strat_data.get("atr_period", 14)),
                stop_atr_mult=float(strat_data.get("stop_atr_mult", 3.0)),
                risk_pct=float(strat_data.get("risk_pct", 0.02)),
                enabled=strat_data.get("enabled", True),
            )
        elif bot_name == "btc_momentum_1h":
            momentum_cfg = BTCMomentum1hConfig(
                rsi_period=int(strat_data.get("rsi_period", 14)),
                adx_threshold=float(strat_data.get("adx_threshold", 20.0)),
                rsi_long_min=float(strat_data.get("rsi_long_min", 35.0)),
                rsi_long_max=float(strat_data.get("rsi_long_max", 50.0)),
                rsi_short_min=float(strat_data.get("rsi_short_min", 50.0)),
                rsi_short_max=float(strat_data.get("rsi_short_max", 65.0)),
                atr_period=int(strat_data.get("atr_period", 14)),
                stop_atr_mult=float(strat_data.get("stop_atr_mult", 3.0)),
                max_hold=int(strat_data.get("max_hold", 16)),
                risk_pct=float(strat_data.get("risk_pct", 0.02)),
                enabled=strat_data.get("enabled", True),
            )

        return cls(
            exchange=exchange,
            risk=risk,
            execution=execution,
            log_level=os.getenv("BOT_LOG_LEVEL", "INFO"),
            btc_trend_4h=trend_cfg,
            btc_momentum_1h=momentum_cfg,
        )

    # -----------------------------------------------------------------------
    # Legacy compatibility — kept for any external callers
    # -----------------------------------------------------------------------

    @classmethod
    def from_env(cls, bot_dir: Optional[str] = None) -> "BotConfig":
        """Legacy loader. Delegates to from_yaml.

        Args:
            bot_dir: Path to bot directory. If None, searches for
                     config.yaml in the caller's bot directory.
        """
        if bot_dir is not None:
            return cls.from_yaml(bot_dir)

        # Fallback: try to discover the bot directory from the call stack
        import inspect
        frame = inspect.currentframe()
        try:
            while frame:
                fname = frame.f_globals.get("__file__", "")
                if fname and "bots/" in fname:
                    # Extract bot directory
                    parts = Path(fname).parts
                    for i, p in enumerate(parts):
                        if p == "bots" and i + 1 < len(parts):
                            bot_dir = str(Path(fname).parent)
                            return cls.from_yaml(bot_dir)
                frame = frame.f_back
        finally:
            del frame

        raise FileNotFoundError(
            "Could not auto-discover bot directory. "
            "Pass bot_dir explicitly: BotConfig.from_yaml('bots/btc_trend_4h')"
        )

    # -----------------------------------------------------------------------
    # Validation
    # -----------------------------------------------------------------------

    def validate(self) -> list[str]:
        """Return list of validation errors (empty = OK)."""
        errors = []

        # Venue validation
        if self.exchange.venue not in SUPPORTED_VENUES:
            errors.append(
                "exchange.venue must be one of supported venues: "
                f"{', '.join(sorted(SUPPORTED_VENUES))}. "
                "Planned (not implemented yet): "
                f"{', '.join(sorted(PLANNED_VENUES))}"
            )

        # Credential checks per venue
        if self.exchange.venue == "binance":
            if not self.exchange.api_key:
                errors.append("BINANCE_API_KEY not set in .env")
            if not self.exchange.api_secret:
                errors.append("BINANCE_API_SECRET not set in .env")

        if self.exchange.venue == "hyperliquid":
            if not self.exchange.wallet_address:
                errors.append("HYPERLIQUID_WALLET_ADDRESS not set in .env")
            if not self.exchange.private_key:
                errors.append("HYPERLIQUID_PRIVATE_KEY not set in .env")

            if "USDC" not in self.exchange.symbol.upper():
                errors.append(
                    "For Hyperliquid, exchange.symbol should be BTC/USDC:USDC"
                )

        # Risk validation
        if self.risk.max_drawdown_pct <= 0 or self.risk.max_drawdown_pct > 0.50:
            errors.append("risk.max_drawdown_pct must be between 0 and 0.50")

        if self.risk.max_daily_loss_pct <= 0 or self.risk.max_daily_loss_pct > 0.50:
            errors.append("risk.max_daily_loss_pct must be between 0 and 0.50")

        # Strategy-specific validation
        if self.btc_trend_4h:
            if self.btc_trend_4h.risk_pct <= 0 or self.btc_trend_4h.risk_pct > 0.10:
                errors.append("btc_trend_4h strategy.risk_pct must be between 0 and 0.10")

        if self.btc_momentum_1h:
            if self.btc_momentum_1h.risk_pct <= 0 or self.btc_momentum_1h.risk_pct > 0.10:
                errors.append("btc_momentum_1h strategy.risk_pct must be between 0 and 0.10")

        return errors
