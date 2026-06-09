"""Tests for exchange adapter behavior with mocked ccxt clients."""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from exchanges.binance.adapters import binance_futures
from exchanges.hyperliquid.adapters import hyperliquid_perps
from shared.python.config import ExchangeConfig


class _FakeExchange:
    def __init__(self):
        self.urls = {"api": {}}
        self.set_sandbox_mode = Mock()
        self.load_markets = AsyncMock()
        self.close = AsyncMock()
        self.fetch_balance = AsyncMock(return_value={"total": {"USDT": 1000}})
        self.fetch_ticker = AsyncMock(return_value={"last": 42000})
        self.create_market_order = AsyncMock(return_value={"id": "m1"})
        self.create_order = AsyncMock(return_value={"id": "o1"})
        self.fetch_positions = AsyncMock(return_value=[])
        self.set_leverage = AsyncMock()


def test_binance_adapter_core_paths(monkeypatch):
    fake = _FakeExchange()
    monkeypatch.setattr(binance_futures.ccxt_async, "binance", lambda _: fake)

    cfg = ExchangeConfig(
        venue="binance",
        api_key="k",
        api_secret="s",
        symbol="BTC/USDT:USDT",
        leverage=5,
        testnet=True,
    )
    adapter = binance_futures.BinanceFutures(cfg)

    asyncio.run(adapter.connect())
    fake.set_sandbox_mode.assert_called_once_with(True)
    fake.set_leverage.assert_awaited_once_with(5, "BTC/USDT:USDT")

    bal = asyncio.run(adapter.fetch_balance())
    tkr = asyncio.run(adapter.fetch_ticker("BTC/USDT:USDT"))
    assert bal["total"]["USDT"] == 1000
    assert tkr["last"] == 42000

    asyncio.run(adapter.place_market_order("BTC/USDT:USDT", "buy", 0.01, reduce_only=True))
    fake.create_market_order.assert_awaited_once()

    asyncio.run(adapter.place_stop_order("BTC/USDT:USDT", "sell", 0.01, 41000))
    fake.create_order.assert_awaited()

    fake.fetch_positions.return_value = [{"symbol": "BTC/USDT:USDT", "contracts": 1}]
    pos = asyncio.run(adapter.fetch_position("BTC/USDT:USDT"))
    assert pos is not None

    assert adapter.get_equity({"total": {"USDT": 2500}}) == 2500.0
    assert adapter.get_equity({"total": {}, "info": {"totalWalletBalance": "123.4"}}) == 123.4


def test_hyperliquid_adapter_core_paths(monkeypatch):
    fake = _FakeExchange()
    monkeypatch.setattr(hyperliquid_perps.ccxt_async, "hyperliquid", lambda _: fake)

    cfg = ExchangeConfig(
        venue="hyperliquid",
        wallet_address="0xabc",
        private_key="0xdef",
        symbol="BTC/USDC:USDC",
        testnet=True,
    )
    adapter = hyperliquid_perps.HyperliquidPerps(cfg)

    asyncio.run(adapter.connect())
    fake.set_sandbox_mode.assert_called_once_with(True)
    fake.load_markets.assert_awaited_once()

    asyncio.run(adapter.fetch_balance())
    asyncio.run(adapter.fetch_ticker("BTC/USDC:USDC"))

    asyncio.run(adapter.place_market_order("BTC/USDC:USDC", "buy", 0.01, reduce_only=True))
    fake.create_market_order.assert_awaited_once()

    with pytest.raises(RuntimeError):
        asyncio.run(adapter.place_stop_order("BTC/USDC:USDC", "sell", 0.01, 41000))

    fake.fetch_positions.return_value = [{"symbol": "BTC/USDC:USDC", "contracts": "2"}]
    pos = asyncio.run(adapter.fetch_position("BTC/USDC:USDC"))
    assert pos is not None

    assert adapter.get_equity({"total": {"USDC": "3000"}}) == 3000.0
    assert adapter.get_equity({"total": {}, "info": {"accountValue": "200"}}) == 200.0


def test_adapter_error_propagation(monkeypatch):
    fake = _FakeExchange()
    fake.fetch_balance.side_effect = RuntimeError("api failure")
    monkeypatch.setattr(binance_futures.ccxt_async, "binance", lambda _: fake)

    adapter = binance_futures.BinanceFutures(ExchangeConfig(venue="binance", api_key="k", api_secret="s"))
    asyncio.run(adapter.connect())

    with pytest.raises(RuntimeError, match="api failure"):
        asyncio.run(adapter.fetch_balance())


def test_adapter_network_timeout(monkeypatch):
    fake = _FakeExchange()
    fake.fetch_ticker.side_effect = asyncio.TimeoutError("network timeout")
    monkeypatch.setattr(hyperliquid_perps.ccxt_async, "hyperliquid", lambda _: fake)

    adapter = hyperliquid_perps.HyperliquidPerps(ExchangeConfig(venue="hyperliquid", wallet_address="w", private_key="p"))
    asyncio.run(adapter.connect())

    with pytest.raises(asyncio.TimeoutError, match="network timeout"):
        asyncio.run(adapter.fetch_ticker("BTC/USDC:USDC"))
