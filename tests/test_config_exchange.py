"""Tests for exchange selection/config validation."""

from bots.shared.config import BotConfig
from bots.shared.exchange import BinanceFutures, HyperliquidPerps, create_exchange


def test_create_exchange_binance_default():
    cfg = BotConfig()
    cfg.exchange.exchange = "binance"
    adapter = create_exchange(cfg.exchange)
    assert isinstance(adapter, BinanceFutures)


def test_create_exchange_hyperliquid():
    cfg = BotConfig()
    cfg.exchange.exchange = "hyperliquid"
    adapter = create_exchange(cfg.exchange)
    assert isinstance(adapter, HyperliquidPerps)


def test_validate_requires_hyperliquid_signer_fields():
    cfg = BotConfig()
    cfg.exchange.exchange = "hyperliquid"
    cfg.exchange.symbol = "BTC/USDC:USDC"
    errors = cfg.validate()
    assert "HYPERLIQUID_WALLET_ADDRESS not set" in errors
    assert "HYPERLIQUID_PRIVATE_KEY not set" in errors
