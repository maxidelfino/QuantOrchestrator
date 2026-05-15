"""Template strategy copied from btc_trend_4h with TODO markers."""

from __future__ import annotations

from typing import Optional

import pandas as pd

from shared.python.strategy import Bar, Position, Signal


class TemplateStrategy:
    """Template strategy engine.

    TODO: Rename this class and replace logic with your strategy edge.
    """

    def __init__(
        self,
        ema_fast: int = 50,
        ema_slow: int = 200,
        ema_regime_daily: int = 200,
        atr_period: int = 14,
        stop_atr_mult: float = 3.0,
    ):
        # TODO: Replace with parameters your strategy actually needs.
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow
        self.ema_regime_daily = ema_regime_daily
        self.atr_period = atr_period
        self.stop_atr_mult = stop_atr_mult

    def compute_indicators(self, df_4h: pd.DataFrame, df_daily: pd.DataFrame) -> pd.DataFrame:
        """Compute indicators.

        TODO: Rename inputs/timeframes to match your strategy.
        """
        out = df_4h.copy()

        out["ema50"] = out["close"].ewm(span=self.ema_fast, adjust=False).mean()
        out["ema200"] = out["close"].ewm(span=self.ema_slow, adjust=False).mean()

        prev_close = out["close"].shift(1)
        tr = pd.concat([
            out["high"] - out["low"],
            (out["high"] - prev_close).abs(),
            (out["low"] - prev_close).abs(),
        ], axis=1).max(axis=1)
        out["atr"] = tr.ewm(alpha=1.0 / self.atr_period, adjust=False).mean()

        daily = df_daily.copy()
        daily["ema200_daily"] = daily["close"].ewm(span=self.ema_regime_daily, adjust=False).mean()
        out["ema200_daily"] = daily[["ema200_daily"]].reindex(out.index, method="ffill")

        return out

    def evaluate(self, bar: Bar, position: Optional[Position]) -> Signal:
        if position is None:
            return self._entry_signal(bar)
        return self._exit_signal(bar, position)

    def _entry_signal(self, bar: Bar) -> Signal:
        # TODO: Replace with your entry conditions.
        if (bar.close > bar.ema50 > bar.ema200 and bar.close > bar.ema200_daily):
            return Signal.LONG
        if (bar.close < bar.ema50 < bar.ema200 and bar.close < bar.ema200_daily):
            return Signal.SHORT
        return Signal.NONE

    def _exit_signal(self, bar: Bar, position: Position) -> Signal:
        # TODO: Replace with your exit logic.
        if position.side == "long":
            if bar.low <= position.stop_price or bar.ema50 < bar.ema200:
                return Signal.CLOSE_LONG
        elif position.side == "short":
            if bar.high >= position.stop_price or bar.ema50 > bar.ema200:
                return Signal.CLOSE_SHORT
        return Signal.NONE

    def calc_stop_price(self, price: float, atr: float, side: str) -> float:
        if side == "long":
            return price - self.stop_atr_mult * atr
        return price + self.stop_atr_mult * atr

    def calc_position_size(self, equity: float, entry_price: float, stop_price: float) -> float:
        # TODO: Tie risk sizing to your config (risk_pct, account constraints, venue minimums).
        risk_usd = equity * 0.02
        dist = abs(entry_price - stop_price)
        if dist <= 0:
            return 0.0
        return risk_usd / dist
