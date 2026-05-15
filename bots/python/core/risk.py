"""
Risk Manager — Capital Protection

Enforces:
- Position sizing limits
- Daily loss limits
- Max drawdown kill switch
- Order size validation
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

from bots.python.core.config import RiskConfig

logger = logging.getLogger(__name__)


@dataclass
class RiskState:
    """Mutable risk tracking state."""
    initial_equity: float = 0.0
    peak_equity: float = 0.0
    daily_pnl: float = 0.0
    daily_start_equity: float = 0.0
    last_reset_date: Optional[date] = None
    is_halted: bool = False
    halt_reason: str = ""


class RiskManager:
    """Enforces risk limits and capital protection."""

    def __init__(self, config: RiskConfig):
        self.config = config
        self.state = RiskState()

    def initialize(self, initial_equity: float) -> None:
        """Set initial equity and reset daily tracking."""
        self.state.initial_equity = initial_equity
        self.state.peak_equity = initial_equity
        self.state.daily_start_equity = initial_equity
        self.state.daily_pnl = 0.0
        self.state.last_reset_date = date.today()
        self.state.is_halted = False
        self.state.halt_reason = ""
        logger.info(f"Risk manager initialized: equity={initial_equity}")

    def update(self, current_equity: float) -> None:
        """Update risk state with current equity. Check all limits."""
        today = date.today()

        # Reset daily tracking if new day
        if self.state.last_reset_date != today:
            self.state.daily_start_equity = current_equity
            self.state.daily_pnl = 0.0
            self.state.last_reset_date = today

        # Update peak
        if current_equity > self.state.peak_equity:
            self.state.peak_equity = current_equity

        # Calculate daily PnL
        self.state.daily_pnl = current_equity - self.state.daily_start_equity

        # Check kill switches
        self._check_daily_loss(current_equity)
        self._check_drawdown(current_equity)

    def _check_daily_loss(self, current_equity: float) -> None:
        """Halt trading if daily loss exceeds limit."""
        if self.state.daily_start_equity <= 0:
            return

        daily_loss_pct = -self.state.daily_pnl / self.state.daily_start_equity
        if daily_loss_pct >= self.config.max_daily_loss_pct:
            self.state.is_halted = True
            self.state.halt_reason = f"Daily loss limit: {daily_loss_pct:.2%}"
            logger.critical(
                f"HALTED: Daily loss {daily_loss_pct:.2%} >= limit {self.config.max_daily_loss_pct:.2%}"
            )

    def _check_drawdown(self, current_equity: float) -> None:
        """Halt trading if drawdown from peak exceeds limit."""
        if self.state.peak_equity <= 0:
            return

        drawdown = (self.state.peak_equity - current_equity) / self.state.peak_equity
        if drawdown >= self.config.max_drawdown_pct:
            self.state.is_halted = True
            self.state.halt_reason = f"Max drawdown: {drawdown:.2%}"
            logger.critical(
                f"HALTED: Drawdown {drawdown:.2%} >= limit {self.config.max_drawdown_pct:.2%}"
            )

    def validate_order_size(self, quantity: float, price: float) -> tuple[bool, str]:
        """Validate order size against limits."""
        order_value = quantity * price

        # Minimum order size
        if order_value < self.config.min_order_size_usd:
            return False, f"Order too small: ${order_value:.2f} < ${self.config.min_order_size_usd}"

        # Maximum position size
        if self.state.peak_equity > 0:
            position_pct = order_value / self.state.peak_equity
            if position_pct > self.config.max_position_pct:
                return False, f"Position too large: {position_pct:.2%} > {self.config.max_position_pct:.2%}"

        return True, ""

    def can_trade(self) -> tuple[bool, str]:
        """Check if trading is allowed."""
        if self.state.is_halted:
            return False, f"Trading halted: {self.state.halt_reason}"
        return True, ""

    def get_status(self) -> dict:
        """Return current risk status."""
        current_equity = self.state.peak_equity + self.state.daily_pnl
        drawdown = 0.0
        if self.state.peak_equity > 0:
            drawdown = max(0, (self.state.peak_equity - current_equity) / self.state.peak_equity)

        daily_loss_pct = 0.0
        if self.state.daily_start_equity > 0:
            daily_loss_pct = -self.state.daily_pnl / self.state.daily_start_equity

        return {
            "equity": self.state.peak_equity,
            "peak_equity": self.state.peak_equity,
            "daily_pnl": self.state.daily_pnl,
            "daily_loss_pct": round(daily_loss_pct, 4),
            "drawdown_pct": round(drawdown, 4),
            "is_halted": self.state.is_halted,
            "halt_reason": self.state.halt_reason,
        }
