"""
BTC Trend-Following 4h — EMA trend-following with daily regime filter

Signals:
  Long:  close > EMA50 > EMA200 AND close > EMA200_daily
  Short: close < EMA50 < EMA200 AND close < EMA200_daily
  Exit:  EMA cross reversal OR stop loss (ATR * mult)
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

from shared.python.strategy import Bar, Position, Signal


class BTCTrend4hStrategy:
    """BTC 4h trend-following strategy engine.

    Long-short trend following on 4h timeframe with daily EMA200 regime filter.
    """

    def __init__(
        self,
        ema_fast: int = 50,
        ema_slow: int = 200,
        ema_regime_daily: int = 200,
        atr_period: int = 14,
        stop_atr_mult: float = 3.0,
    ):
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow
        self.ema_regime_daily = ema_regime_daily
        self.atr_period = atr_period
        self.stop_atr_mult = stop_atr_mult

    def compute_indicators(self, df_4h: pd.DataFrame, df_daily: pd.DataFrame) -> pd.DataFrame:
        """Compute all indicators for the strategy."""
        out = df_4h.copy()

        # 4h EMAs
        out["ema50"] = out["close"].ewm(span=self.ema_fast, adjust=False).mean()
        out["ema200"] = out["close"].ewm(span=self.ema_slow, adjust=False).mean()

        # ATR
        prev_close = out["close"].shift(1)
        tr = pd.concat([
            out["high"] - out["low"],
            (out["high"] - prev_close).abs(),
            (out["low"] - prev_close).abs(),
        ], axis=1).max(axis=1)
        out["atr"] = tr.ewm(alpha=1.0 / self.atr_period, adjust=False).mean()

        # Daily regime
        daily = df_daily.copy()
        daily["ema200_daily"] = daily["close"].ewm(span=self.ema_regime_daily, adjust=False).mean()
        out["ema200_daily"] = daily[["ema200_daily"]].reindex(out.index, method="ffill")

        return out

    def evaluate(self, bar: Bar, position: Optional[Position]) -> Signal:
        """Evaluate strategy logic for current bar.

        Returns the signal to act on.
        """
        if position is None:
            return self._entry_signal(bar)
        return self._exit_signal(bar, position)

    def _entry_signal(self, bar: Bar) -> Signal:
        """Check for new entry signals."""
        ema50 = bar.indicator("ema50")
        ema200 = bar.indicator("ema200")
        ema200_daily = bar.indicator("ema200_daily")

        # Long: close > EMA50 > EMA200 AND regime is bullish
        if (bar.close > ema50 > ema200
                and bar.close > ema200_daily):
            return Signal.LONG

        # Short: close < EMA50 < EMA200 AND regime is bearish
        if (bar.close < ema50 < ema200
                and bar.close < ema200_daily):
            return Signal.SHORT

        return Signal.NONE

    def _exit_signal(self, bar: Bar, position: Position) -> Signal:
        """Check for exit conditions."""
        ema50 = bar.indicator("ema50")
        ema200 = bar.indicator("ema200")

        if position.side == "long":
            # Stop loss hit
            if bar.low <= position.stop_price:
                return Signal.CLOSE_LONG
            # Trend reversal: EMA50 crossed below EMA200
            if ema50 < ema200:
                return Signal.CLOSE_LONG

        elif position.side == "short":
            # Stop loss hit
            if bar.high >= position.stop_price:
                return Signal.CLOSE_SHORT
            # Trend reversal: EMA50 crossed above EMA200
            if ema50 > ema200:
                return Signal.CLOSE_SHORT

        return Signal.NONE

    def calc_stop_price(self, price: float, atr: float, side: str) -> float:
        """Calculate stop loss price."""
        if side == "long":
            return price - self.stop_atr_mult * atr
        return price + self.stop_atr_mult * atr

    def calc_position_size(self, equity: float, entry_price: float, stop_price: float) -> float:
        """Calculate position size using risk-per-trade model.

        size = (equity * risk_pct) / |entry - stop|
        """
        risk_usd = equity * 0.02  # 2% risk
        dist = abs(entry_price - stop_price)
        if dist <= 0:
            return 0.0
        return risk_usd / dist
