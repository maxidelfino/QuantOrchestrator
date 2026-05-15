"""Core TradingBot behavior tests."""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from shared.python.bot import TradingBot
from shared.python.config import BotConfig
from shared.python.state import BotState
from shared.python.strategy import Bar, Position, Signal


class DummyEngine:
    def calc_stop_price(self, price: float, atr: float, side: str) -> float:
        return price - atr if side == "long" else price + atr

    def calc_position_size(self, equity: float, entry_price: float, stop_price: float) -> float:
        return 0.1

    def evaluate(self, bar: Bar, position: Position | None) -> Signal:
        return Signal.NONE


class FakeExchange:
    supports_native_stop = False

    def __init__(self):
        self.fail_fetch_order = False

    async def connect(self):
        return None

    async def close(self):
        return None

    async def fetch_klines(self, symbol: str, timeframe: str, limit: int = 500):
        raise NotImplementedError

    async def fetch_daily_klines(self, symbol: str, limit: int = 500):
        raise NotImplementedError

    async def fetch_ticker(self, symbol: str):
        return {"last": 100.0}

    async def fetch_balance(self):
        return {"equity": 10_000.0}

    async def fetch_position(self, symbol: str):
        return None

    async def place_market_order(self, symbol: str, side: str, amount: float, reduce_only: bool = False):
        return {"id": "mkt-1", "average": 100.0}

    async def place_limit_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        price: float,
        reduce_only: bool = False,
        post_only: bool = False,
    ):
        return {"id": "lim-1", "status": "open", "average": price}

    async def fetch_order(self, symbol: str, order_id: str):
        if self.fail_fetch_order:
            raise RuntimeError("temporary API error")
        return {"id": order_id, "status": "open", "average": 100.0}

    async def cancel_order(self, symbol: str, order_id: str):
        return {"id": order_id, "status": "canceled"}

    async def cancel_all_orders(self, symbol: str):
        return []

    async def fetch_open_orders(self, symbol: str):
        return []

    async def fetch_order_book(self, symbol: str, limit: int = 1):
        return {"bids": [[99, 1]], "asks": [[101, 1]]}

    def get_equity(self, balance):
        return float(balance.get("equity", 0))


@pytest.fixture
def bot(tmp_path, monkeypatch):
    import shared.python.bot as bot_module

    fake = FakeExchange()
    monkeypatch.setattr(bot_module, "create_exchange", lambda _cfg: fake)

    cfg = BotConfig()
    cfg.state_dir = str(tmp_path / "state")
    cfg.execution.entry_order_type = "limit"
    cfg.execution.entry_ttl_bars = 2

    trading_bot = TradingBot(cfg, {"s1": (DummyEngine(), "1h")})
    trading_bot.state_mgr.connect()
    trading_bot.risk.initialize(10_000.0)

    yield trading_bot
    trading_bot.state_mgr.close()


def test_order_lifecycle_open_then_close(bot):
    ctx = bot._strategies["s1"]
    bar = Bar(
        time=datetime(2026, 1, 1, 0, 0, 0),
        open=100,
        high=101,
        low=99,
        close=100,
        volume=1,
        atr=2,
    )

    asyncio.run(bot._open_position("s1", ctx, "long", bar, 10_000.0, "BTC/USDC:USDC", DummyEngine()))
    assert ctx.pending_limit_order is not None

    ctx.position = Position("long", 100.0, 0.1, 98.0, datetime(2026, 1, 1, 0, 0, 0))
    ctx.pending_limit_order = None
    asyncio.run(bot._close_position("s1", "BTC/USDC:USDC", "strategy_exit"))
    assert ctx.position is None


def test_limit_ttl_tracking_expires_and_fallbacks(bot):
    ctx = bot._strategies["s1"]
    ctx.pending_limit_order = {
        "order_id": "lim-1",
        "side": "buy",
        "quantity": 0.1,
        "price": 100.0,
        "strategy_side": "long",
        "stop_price": 98.0,
        "bar_placed": "2026-01-01T00:00:00",
        "entry_time_iso": "2026-01-01T00:00:00",
    }
    state = BotState(last_bar_time={"s1": "2026-01-01T02:00:00"})

    asyncio.run(bot._check_pending_orders("BTC/USDC:USDC", state))
    assert ctx.pending_limit_order is None
    assert ctx.position is not None


def test_startup_reconciliation_clears_missing_pending_orders(bot):
    ctx = bot._strategies["s1"]
    ctx.pending_limit_order = {"order_id": "ghost", "price": 100.0}
    state = bot.state_mgr.load()

    asyncio.run(bot._reconcile_startup_state(state))
    assert ctx.pending_limit_order is None


def test_error_recovery_when_order_status_api_fails(bot):
    ctx = bot._strategies["s1"]
    ctx.pending_limit_order = {
        "order_id": "lim-1",
        "side": "buy",
        "quantity": 0.1,
        "price": 100.0,
        "strategy_side": "long",
        "stop_price": 98.0,
        "bar_placed": "2026-01-01T00:00:00",
        "entry_time_iso": "2026-01-01T00:00:00",
    }
    bot.exchange.fail_fetch_order = True

    asyncio.run(bot._check_pending_orders("BTC/USDC:USDC", BotState(last_bar_time={"s1": "2026-01-01T01:00:00"})))
    assert ctx.pending_limit_order is not None
    assert ctx.position is None


def test_risk_halt_closes_existing_position(bot):
    ctx = bot._strategies["s1"]
    ctx.position = Position("long", 100.0, 0.1, 98.0, datetime(2026, 1, 1, 0, 0, 0))
    bot.risk.state.is_halted = True
    bot.risk.state.halt_reason = "manual test halt"

    asyncio.run(bot._tick())
    assert ctx.position is None
