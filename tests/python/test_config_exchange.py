"""Tests for exchange selection/config validation."""

from shared.python.config import BotConfig
from exchanges.binance.adapters.binance_futures import BinanceFutures
from exchanges.hyperliquid.adapters.hyperliquid_perps import HyperliquidPerps
from shared.python.exchange import create_exchange


def test_create_exchange_binance_default():
    cfg = BotConfig()
    cfg.exchange.venue = "binance"
    adapter = create_exchange(cfg.exchange)
    assert isinstance(adapter, BinanceFutures)


def test_create_exchange_hyperliquid():
    cfg = BotConfig()
    cfg.exchange.venue = "hyperliquid"
    adapter = create_exchange(cfg.exchange)
    assert isinstance(adapter, HyperliquidPerps)


def test_validate_requires_hyperliquid_signer_fields():
    cfg = BotConfig()
    cfg.exchange.venue = "hyperliquid"
    cfg.exchange.symbol = "BTC/USDC:USDC"
    errors = cfg.validate()
    assert "HYPERLIQUID_WALLET_ADDRESS not set in .env" in errors
    assert "HYPERLIQUID_PRIVATE_KEY not set in .env" in errors
