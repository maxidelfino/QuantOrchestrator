"""Tests for BTC Momentum 1h strategy logic."""

import pandas as pd
import pytest

from exchanges.hyperliquid.bots.btc_momentum_1h.strategy import BTCMomentum1hStrategy
from shared.python.strategy import Bar, Position, Signal


@pytest.fixture
def strategy():
    return BTCMomentum1hStrategy()


class TestEntrySignals:
    def test_long_entry_when_all_conditions_match(self, strategy):
        strategy._state.prev2_rsi = 40.0
        bar = Bar(
            time=pd.Timestamp("2024-01-01T00:00:00Z"),
            open=42000,
            high=42600,
            low=41800,
            close=42500,
            volume=1000,
            rsi=45.0,
            adx=25.0,
            plus_di=30.0,
            minus_di=15.0,
        )
        assert strategy.evaluate(bar, None) == Signal.LONG

    def test_short_entry_when_all_conditions_match(self, strategy):
        strategy._state.prev2_rsi = 60.0
        bar = Bar(
            time=pd.Timestamp("2024-01-01T00:00:00Z"),
            open=42500,
            high=42600,
            low=41800,
            close=42000,
            volume=1000,
            rsi=55.0,
            adx=30.0,
            plus_di=12.0,
            minus_di=28.0,
        )
        assert strategy.evaluate(bar, None) == Signal.SHORT

    def test_no_entry_when_adx_below_threshold(self, strategy):
        strategy._state.prev2_rsi = 35.0
        bar = Bar(
            time=pd.Timestamp("2024-01-01T00:00:00Z"),
            open=42000,
            high=42600,
            low=41800,
            close=42500,
            volume=1000,
            rsi=45.0,
            adx=19.9,
            plus_di=30.0,
            minus_di=15.0,
        )
        assert strategy.evaluate(bar, None) == Signal.NONE

    def test_no_entry_when_rsi_out_of_range(self, strategy):
        strategy._state.prev2_rsi = 30.0
        bar = Bar(
            time=pd.Timestamp("2024-01-01T00:00:00Z"),
            open=42000,
            high=42600,
            low=41800,
            close=42500,
            volume=1000,
            rsi=70.0,
            adx=25.0,
            plus_di=30.0,
            minus_di=15.0,
        )
        assert strategy.evaluate(bar, None) == Signal.NONE

    def test_no_entry_when_rsi_momentum_missing(self, strategy):
        strategy._state.prev2_rsi = 50.0
        bar = Bar(
            time=pd.Timestamp("2024-01-01T00:00:00Z"),
            open=42000,
            high=42600,
            low=41800,
            close=42500,
            volume=1000,
            rsi=45.0,
            adx=25.0,
            plus_di=30.0,
            minus_di=15.0,
        )
        assert strategy.evaluate(bar, None) == Signal.NONE

    def test_no_entry_when_candle_direction_or_di_invalid(self, strategy):
        strategy._state.prev2_rsi = 40.0
        bar = Bar(
            time=pd.Timestamp("2024-01-01T00:00:00Z"),
            open=42000,
            high=42600,
            low=41800,
            close=42000,  # doji: not bullish/bearish
            volume=1000,
            rsi=45.0,
            adx=25.0,
            plus_di=10.0,
            minus_di=20.0,
        )
        assert strategy.evaluate(bar, None) == Signal.NONE


class TestExitSignals:
    def test_close_long_on_trailing_stop(self, strategy):
        position = Position("long", 42000, 0.1, 41000, pd.Timestamp("2024-01-01T00:00:00Z"))
        bar = Bar(
            time=pd.Timestamp("2024-01-01T05:00:00Z"),
            open=42000,
            high=42200,
            low=40900,
            close=41500,
            volume=1000,
        )
        assert strategy.evaluate(bar, position) == Signal.CLOSE_LONG

    def test_close_short_on_trailing_stop(self, strategy):
        position = Position("short", 42000, 0.1, 43000, pd.Timestamp("2024-01-01T00:00:00Z"))
        bar = Bar(
            time=pd.Timestamp("2024-01-01T05:00:00Z"),
            open=42000,
            high=43100,
            low=41800,
            close=42900,
            volume=1000,
        )
        assert strategy.evaluate(bar, position) == Signal.CLOSE_SHORT

    def test_close_on_max_hold_bars(self, strategy):
        position = Position("long", 42000, 0.1, 41000, pd.Timestamp("2024-01-01T00:00:00Z"))
        bar = Bar(
            time=pd.Timestamp("2024-01-01T16:00:00Z"),
            open=42000,
            high=42500,
            low=41500,
            close=42200,
            volume=1000,
        )
        assert strategy.evaluate(bar, position) == Signal.CLOSE_LONG


class TestSizingAndBars:
    def test_position_size_uses_two_percent_risk(self, strategy):
        # risk = 10000 * 0.02 = 200, stop distance = 1000, size = 0.2
        size = strategy.calc_position_size(10000, 42000, 41000)
        assert size == pytest.approx(0.2)

    def test_bar_count_with_mixed_timestamps(self, strategy):
        position = Position("long", 42000, 0.1, 41000, pd.Timestamp("2024-01-01 00:00:00"))
        assert strategy._bar_count(position, pd.Timestamp("2024-01-01 03:00:00+00:00")) == 3

    def test_bar_count_never_negative(self, strategy):
        position = Position("long", 42000, 0.1, 41000, pd.Timestamp("2024-01-01T05:00:00Z"))
        assert strategy._bar_count(position, pd.Timestamp("2024-01-01T03:00:00Z")) == 0
