"""Tests for risk manager."""

from datetime import date, timedelta

import pytest

from bot.config import RiskConfig
from bot.risk import RiskManager


@pytest.fixture
def risk_mgr():
    return RiskManager(RiskConfig())


class TestRiskManager:
    def test_initialize(self, risk_mgr):
        risk_mgr.initialize(10000)
        assert risk_mgr.state.initial_equity == 10000
        assert risk_mgr.state.peak_equity == 10000
        assert risk_mgr.state.daily_start_equity == 10000
        assert not risk_mgr.state.is_halted

    def test_update_peak_equity(self, risk_mgr):
        risk_mgr.initialize(10000)
        risk_mgr.update(11000)
        assert risk_mgr.state.peak_equity == 11000

    def test_daily_pnl_tracking(self, risk_mgr):
        risk_mgr.initialize(10000)
        risk_mgr.update(9500)
        assert risk_mgr.state.daily_pnl == -500

    def test_daily_loss_halt(self, risk_mgr):
        """Halt when daily loss exceeds 5%."""
        risk_mgr.initialize(10000)
        risk_mgr.update(9400)  # 6% loss
        assert risk_mgr.state.is_halted
        assert "Daily loss" in risk_mgr.state.halt_reason

    def test_drawdown_halt(self, risk_mgr):
        """Halt when drawdown from peak exceeds 25%."""
        risk_mgr.initialize(10000)
        risk_mgr.update(15000)  # New peak
        risk_mgr.update(11000)  # 26.7% drawdown from peak
        assert risk_mgr.state.is_halted
        assert "drawdown" in risk_mgr.state.halt_reason.lower()

    def test_can_trade_when_ok(self, risk_mgr):
        risk_mgr.initialize(10000)
        risk_mgr.update(10500)
        can_trade, reason = risk_mgr.can_trade()
        assert can_trade
        assert reason == ""

    def test_cannot_trade_when_halted(self, risk_mgr):
        risk_mgr.initialize(10000)
        risk_mgr.update(9400)  # Trigger halt
        can_trade, reason = risk_mgr.can_trade()
        assert not can_trade
        assert "halted" in reason.lower()

    def test_validate_order_size_minimum(self, risk_mgr):
        risk_mgr.initialize(10000)
        valid, reason = risk_mgr.validate_order_size(0.0001, 40000)  # $4 order
        assert not valid
        assert "too small" in reason.lower()

    def test_validate_order_size_maximum(self, risk_mgr):
        risk_mgr.initialize(10000)
        valid, reason = risk_mgr.validate_order_size(1.0, 40000)  # $40k = 400% of equity
        assert not valid
        assert "too large" in reason.lower()

    def test_validate_order_size_ok(self, risk_mgr):
        risk_mgr.initialize(10000)
        valid, reason = risk_mgr.validate_order_size(0.01, 40000)  # $400 = 4% of equity
        assert valid
        assert reason == ""

    def test_daily_reset(self, risk_mgr):
        """Daily PnL should reset on new day."""
        risk_mgr.initialize(10000)
        risk_mgr.update(9500)  # -$500 day
        assert risk_mgr.state.daily_pnl == -500

        # Simulate next day
        risk_mgr.state.last_reset_date = date.today() - timedelta(days=1)
        risk_mgr.update(9600)
        assert risk_mgr.state.daily_pnl == 0  # Reset
        assert risk_mgr.state.daily_start_equity == 9600

    def test_get_status(self, risk_mgr):
        risk_mgr.initialize(10000)
        risk_mgr.update(10500)
        status = risk_mgr.get_status()
        assert status["peak_equity"] == 10500
        assert status["daily_pnl"] == 500
        assert not status["is_halted"]
