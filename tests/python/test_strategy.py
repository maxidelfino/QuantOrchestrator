"""Tests for btc-trend-4h strategy logic (formerly v40)."""

import pandas as pd
import pytest

from bots.python.core.strategy import Bar, Position, Signal
from bots.python.btc_trend_4h.strategy import BTCTrend4hStrategy


@pytest.fixture
def strategy():
    return BTCTrend4hStrategy()


@pytest.fixture
def bull_bar():
    """Bar in bullish regime with uptrend."""
    return Bar(
        time=pd.Timestamp("2024-01-01", tz="UTC"),
        open=42000, high=43000, low=41500, close=42500,
        volume=1000,
        ema50=41000, ema200=40000, ema200_daily=39000, atr=500,
    )


@pytest.fixture
def bear_bar():
    """Bar in bearish regime with downtrend."""
    return Bar(
        time=pd.Timestamp("2024-01-01", tz="UTC"),
        open=38000, high=38500, low=37000, close=37500,
        volume=1000,
        ema50=39000, ema200=40000, ema200_daily=41000, atr=500,
    )


@pytest.fixture
def ranging_bar():
    """Bar in ranging market (no clear trend)."""
    return Bar(
        time=pd.Timestamp("2024-01-01", tz="UTC"),
        open=40000, high=40500, low=39500, close=40100,
        volume=500,
        ema50=40050, ema200=40000, ema200_daily=40200, atr=200,
    )


class TestEntrySignals:
    def test_long_signal_in_bull_market(self, strategy, bull_bar):
        signal = strategy.evaluate(bull_bar, None)
        assert signal == Signal.LONG

    def test_short_signal_in_bear_market(self, strategy, bear_bar):
        signal = strategy.evaluate(bear_bar, None)
        assert signal == Signal.SHORT

    def test_no_signal_in_ranging_market(self, strategy, ranging_bar):
        signal = strategy.evaluate(ranging_bar, None)
        assert signal == Signal.NONE

    def test_no_long_signal_when_regime_is_bearish(self, strategy):
        """Close > EMA50 > EMA200 but daily regime is bearish."""
        bar = Bar(
            time=pd.Timestamp("2024-01-01", tz="UTC"),
            open=42000, high=43000, low=41500, close=42500,
            volume=1000,
            ema50=41000, ema200=40000, ema200_daily=43000, atr=500,
        )
        signal = strategy.evaluate(bar, None)
        assert signal == Signal.NONE


class TestExitSignals:
    def test_close_long_on_trend_reversal(self, strategy):
        """EMA50 crossed below EMA200 while in long position."""
        bar = Bar(
            time=pd.Timestamp("2024-01-01", tz="UTC"),
            open=42000, high=43000, low=41500, close=42500,
            volume=1000,
            ema50=39000, ema200=40000, ema200_daily=39000, atr=500,
        )
        position = Position(
            side="long", entry_price=42000, quantity=0.1,
            stop_price=40500, entry_time=pd.Timestamp("2024-01-01", tz="UTC"),
        )
        signal = strategy.evaluate(bar, position)
        assert signal == Signal.CLOSE_LONG

    def test_close_long_on_stop_loss(self, strategy):
        """Price dropped below stop loss."""
        bar = Bar(
            time=pd.Timestamp("2024-01-01", tz="UTC"),
            open=42000, high=42500, low=40000, close=40500,
            volume=1000,
            ema50=41000, ema200=40000, ema200_daily=39000, atr=500,
        )
        position = Position(
            side="long", entry_price=42000, quantity=0.1,
            stop_price=40500, entry_time=pd.Timestamp("2024-01-01", tz="UTC"),
        )
        signal = strategy.evaluate(bar, position)
        assert signal == Signal.CLOSE_LONG

    def test_close_short_on_trend_reversal(self, strategy):
        """EMA50 crossed above EMA200 while in short position."""
        bar = Bar(
            time=pd.Timestamp("2024-01-01", tz="UTC"),
            open=38000, high=38500, low=37000, close=37500,
            volume=1000,
            ema50=41000, ema200=40000, ema200_daily=41000, atr=500,
        )
        position = Position(
            side="short", entry_price=38000, quantity=0.1,
            stop_price=39500, entry_time=pd.Timestamp("2024-01-01", tz="UTC"),
        )
        signal = strategy.evaluate(bar, position)
        assert signal == Signal.CLOSE_SHORT

    def test_close_short_on_stop_loss(self, strategy):
        """Price rose above stop loss."""
        bar = Bar(
            time=pd.Timestamp("2024-01-01", tz="UTC"),
            open=38000, high=40000, low=37500, close=39000,
            volume=1000,
            ema50=39000, ema200=40000, ema200_daily=41000, atr=500,
        )
        position = Position(
            side="short", entry_price=38000, quantity=0.1,
            stop_price=39500, entry_time=pd.Timestamp("2024-01-01", tz="UTC"),
        )
        signal = strategy.evaluate(bar, position)
        assert signal == Signal.CLOSE_SHORT

    def test_no_exit_when_trend_intact(self, strategy, bull_bar):
        """Long position, trend still bullish."""
        position = Position(
            side="long", entry_price=42000, quantity=0.1,
            stop_price=40500, entry_time=pd.Timestamp("2024-01-01", tz="UTC"),
        )
        signal = strategy.evaluate(bull_bar, position)
        assert signal == Signal.NONE


class TestPositionSizing:
    def test_calc_stop_price_long(self, strategy):
        stop = strategy.calc_stop_price(42000, 500, "long")
        assert stop == 42000 - 3.0 * 500
        assert stop == 40500

    def test_calc_stop_price_short(self, strategy):
        stop = strategy.calc_stop_price(38000, 500, "short")
        assert stop == 38000 + 3.0 * 500
        assert stop == 39500

    def test_calc_position_size(self, strategy):
        """Risk 2% of $10,000 = $200. Stop distance = $1,500. Size = 200/1500 = 0.133."""
        size = strategy.calc_position_size(10000, 42000, 40500)
        assert abs(size - 0.133333) < 0.001

    def test_calc_position_size_zero_distance(self, strategy):
        """Zero stop distance should return zero size."""
        size = strategy.calc_position_size(10000, 42000, 42000)
        assert size == 0.0
