"""Tests for state manager."""

import tempfile
from pathlib import Path

import pytest

from bots.shared.state import BotState, StateManager


@pytest.fixture
def state_mgr():
    with tempfile.TemporaryDirectory() as tmpdir:
        mgr = StateManager(tmpdir)
        mgr.connect()
        yield mgr
        mgr.close()


class TestStateManager:
    def test_create_tables(self, state_mgr):
        """Tables should be created on connect."""
        state = state_mgr.load()
        assert state is not None
        assert not state.is_running

    def test_save_and_load(self, state_mgr):
        state = BotState(
            is_running=True,
            last_bar_time={"btc-trend-4h": "2024-01-01T00:00:00+00:00"},
            last_signal="long",
            error_count=2,
            last_error="test error",
        )
        state_mgr.save(state)

        loaded = state_mgr.load()
        assert loaded.is_running
        assert loaded.last_bar_time == {"btc-trend-4h": "2024-01-01T00:00:00+00:00"}
        assert loaded.last_signal == "long"
        assert loaded.error_count == 2
        assert loaded.last_error == "test error"

    def test_save_position(self, state_mgr):
        state = BotState(
            current_position={
                "side": "long",
                "entry_price": 42000,
                "quantity": 0.1,
                "stop_price": 40500,
                "entry_time": "2024-01-01T00:00:00+00:00",
            }
        )
        state_mgr.save(state)

        loaded = state_mgr.load()
        assert loaded.current_position["side"] == "long"
        assert loaded.current_position["entry_price"] == 42000

    def test_record_trade(self, state_mgr):
        state_mgr.record_trade(
            entry_time="2024-01-01T00:00:00",
            symbol="BTCUSDT",
            side="long",
            entry_price=42000,
            quantity=0.1,
            exit_time="2024-01-02T00:00:00",
            exit_price=43000,
            pnl=100,
            reason="strategy_exit",
        )

        trades = state_mgr.fetch_trades()
        assert len(trades) == 1
        assert trades[0]["symbol"] == "BTCUSDT"
        assert trades[0]["pnl"] == 100

    def test_record_event(self, state_mgr):
        state_mgr.record_event("signal", {"signal": "long", "price": 42000})

        events = state_mgr.fetch_events()
        assert len(events) == 1
        assert events[0]["event_type"] == "signal"

    def test_fetch_trades_limit(self, state_mgr):
        for i in range(20):
            state_mgr.record_trade(
                entry_time=f"2024-01-{i+1:02d}T00:00:00",
                symbol="BTCUSDT",
                side="long",
                entry_price=42000 + i,
                quantity=0.1,
            )

        trades = state_mgr.fetch_trades(limit=5)
        assert len(trades) == 5

    def test_persistence_across_reconnect(self):
        """State should persist across disconnect/reconnect."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr1 = StateManager(tmpdir)
            mgr1.connect()
            state = BotState(is_running=True, last_signal="long")
            mgr1.save(state)
            mgr1.close()

            mgr2 = StateManager(tmpdir)
            mgr2.connect()
            loaded = mgr2.load()
            assert loaded.is_running
            assert loaded.last_signal == "long"
            mgr2.close()
