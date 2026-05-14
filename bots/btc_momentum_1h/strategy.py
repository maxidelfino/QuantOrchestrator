"""
BTC Momentum 1h — RSI Momentum Pullback with ADX filter

Edge: Enter trending markets on RSI pullbacks in the direction of the trend.
  - ADX confirms trend strength
  - DMI confirms trend direction
  - RSI pullback provides entry timing

Entry LONG:
  ADX(14) > 20, RSI(14) in 35-50, RSI rising vs 2 bars ago,
  candle bullish (close > open), +DI > -DI

Entry SHORT:
  ADX(14) > 20, RSI(14) in 50-65, RSI falling vs 2 bars ago,
  candle bearish (close < open), -DI > +DI

Exit:
  Trailing stop 3x ATR, max hold 16 bars (16h)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from bots.shared.strategy import Bar, Position, Signal


@dataclass
class MomentumState:
    """Per-tick state needed for RSI momentum comparison."""
    prev_rsi: Optional[float] = None
    prev2_rsi: Optional[float] = None


class BTCMomentum1hStrategy:
    """BTC 1h RSI Momentum Pullback strategy engine."""

    def __init__(
        self,
        rsi_period: int = 14,
        adx_period: int = 14,
        adx_threshold: float = 20.0,
        rsi_long_min: float = 35.0,
        rsi_long_max: float = 50.0,
        rsi_short_min: float = 50.0,
        rsi_short_max: float = 65.0,
        atr_period: int = 14,
        stop_atr_mult: float = 3.0,
        max_hold_bars: int = 16,
        risk_pct: float = 0.02,
    ):
        self.rsi_period = rsi_period
        self.adx_period = adx_period
        self.adx_threshold = adx_threshold
        self.rsi_long_min = rsi_long_min
        self.rsi_long_max = rsi_long_max
        self.rsi_short_min = rsi_short_min
        self.rsi_short_max = rsi_short_max
        self.atr_period = atr_period
        self.stop_atr_mult = stop_atr_mult
        self.max_hold_bars = max_hold_bars
        self.risk_pct = risk_pct

        # Internal state for RSI comparison
        self._state = MomentumState()

    def compute_indicators(self, df_1h: pd.DataFrame, _df_daily: pd.DataFrame) -> pd.DataFrame:
        """Compute RSI, ADX/DMI, and ATR on 1h data.

        The second parameter is unused (btc-momentum-1h has no daily regime filter)
        but kept to match the interface expected by the orchestrator.
        """
        out = df_1h.copy()

        # RSI
        delta = out["close"].diff()
        gain = delta.where(delta > 0, 0.0)
        loss = (-delta).where(delta < 0, 0.0)
        avg_gain = gain.ewm(alpha=1.0 / self.rsi_period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1.0 / self.rsi_period, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        out["rsi"] = 100.0 - (100.0 / (1.0 + rs))
        out["rsi"] = out["rsi"].fillna(50.0)

        # ATR
        prev_close = out["close"].shift(1)
        tr = pd.concat([
            out["high"] - out["low"],
            (out["high"] - prev_close).abs(),
            (out["low"] - prev_close).abs(),
        ], axis=1).max(axis=1)
        out["atr"] = tr.ewm(alpha=1.0 / self.atr_period, adjust=False).mean()

        # ADX / DMI
        plus_dm = out["high"].diff()
        minus_dm = -out["low"].diff()
        plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
        minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)
        avg_plus_dm = plus_dm.ewm(alpha=1.0 / self.adx_period, adjust=False).mean()
        avg_minus_dm = minus_dm.ewm(alpha=1.0 / self.adx_period, adjust=False).mean()
        atr_smooth = out["atr"].copy()
        plus_di = 100.0 * (avg_plus_dm / atr_smooth.replace(0, np.nan))
        minus_di = 100.0 * (avg_minus_dm / atr_smooth.replace(0, np.nan))
        out["plus_di"] = plus_di.fillna(0.0)
        out["minus_di"] = minus_di.fillna(0.0)

        dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
        out["adx"] = dx.ewm(alpha=1.0 / self.adx_period, adjust=False).mean().fillna(0.0)

        return out

    def evaluate(self, bar: Bar, position: Optional[Position]) -> Signal:
        """Evaluate strategy logic for current bar."""
        if position is None:
            return self._entry_signal(bar)
        return self._exit_signal(bar, position)

    def _entry_signal(self, bar: Bar) -> Signal:
        """Check for entry signals using RSI momentum pullback logic."""
        adx = getattr(bar, "adx", 0.0)
        rsi = getattr(bar, "rsi", 50.0)
        plus_di = getattr(bar, "plus_di", 0.0)
        minus_di = getattr(bar, "minus_di", 0.0)
        prev2_rsi = self._state.prev2_rsi

        # ADX must confirm trend
        if adx < self.adx_threshold:
            return Signal.NONE

        candle_bullish = bar.close > bar.open
        candle_bearish = bar.close < bar.open

        # LONG: RSI pullback in uptrend
        rsi_rising = (prev2_rsi is not None) and (rsi > prev2_rsi)
        if (self.rsi_long_min <= rsi <= self.rsi_long_max
                and rsi_rising
                and candle_bullish
                and plus_di > minus_di):
            return Signal.LONG

        # SHORT: RSI pullback in downtrend
        rsi_falling = (prev2_rsi is not None) and (rsi < prev2_rsi)
        if (self.rsi_short_min <= rsi <= self.rsi_short_max
                and rsi_falling
                and candle_bearish
                and minus_di > plus_di):
            return Signal.SHORT

        return Signal.NONE

    def _exit_signal(self, bar: Bar, position: Position) -> Signal:
        """Check exit conditions: trailing stop and max hold."""
        if position.side == "long":
            if bar.low <= position.stop_price:
                return Signal.CLOSE_LONG
            # Max hold bars
            if self._bar_count(position) >= self.max_hold_bars:
                return Signal.CLOSE_LONG

        elif position.side == "short":
            if bar.high >= position.stop_price:
                return Signal.CLOSE_SHORT
            if self._bar_count(position) >= self.max_hold_bars:
                return Signal.CLOSE_SHORT

        return Signal.NONE

    def _bar_count(self, position: Position) -> int:
        """Count bars since position entry."""
        from datetime import timedelta
        elapsed = position.entry_time.tz_localize(None) if position.entry_time.tzinfo else position.entry_time
        # 1h timeframe
        bars = int((pd.Timestamp.utcnow().replace(tzinfo=None) - elapsed).total_seconds() / 3600)
        return bars

    def calc_stop_price(self, price: float, atr: float, side: str) -> float:
        """Calculate trailing stop loss price."""
        if side == "long":
            return price - self.stop_atr_mult * atr
        return price + self.stop_atr_mult * atr

    def calc_position_size(self, equity: float, entry_price: float, stop_price: float) -> float:
        """Calculate position size using risk-per-trade model.

        size = (equity * risk_pct) / |entry - stop|
        """
        risk_usd = equity * self.risk_pct
        dist = abs(entry_price - stop_price)
        if dist <= 0:
            return 0.0
        return risk_usd / dist

    def update_rsi_state(self, rsi: float) -> None:
        """Update internal RSI history for momentum comparison.

        Call this after each bar evaluation to track RSI values.
        """
        self._state.prev2_rsi = self._state.prev_rsi
        self._state.prev_rsi = rsi
