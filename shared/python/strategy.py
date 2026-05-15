"""
Shared types — Signal enum, Bar, Position dataclasses.

Used by all strategies and the orchestrator.
"""

from __future__ import annotations

from dataclasses import InitVar, dataclass, field
from enum import Enum
from typing import Optional

import pandas as pd


class Signal(Enum):
    NONE = "none"
    LONG = "long"
    SHORT = "short"
    CLOSE_LONG = "close_long"
    CLOSE_SHORT = "close_short"


@dataclass
class Bar:
    """Single OHLCV bar with computed indicators."""
    time: pd.Timestamp
    open: float
    high: float
    low: float
    close: float
    volume: float
    indicators: dict[str, float] = field(default_factory=dict)
    ema50: InitVar[float | None] = None
    ema200: InitVar[float | None] = None
    ema200_daily: InitVar[float | None] = None
    atr: InitVar[float | None] = None
    rsi: InitVar[float | None] = None
    adx: InitVar[float | None] = None
    plus_di: InitVar[float | None] = None
    minus_di: InitVar[float | None] = None

    def __post_init__(
        self,
        ema50: float | None,
        ema200: float | None,
        ema200_daily: float | None,
        atr: float | None,
        rsi: float | None,
        adx: float | None,
        plus_di: float | None,
        minus_di: float | None,
    ) -> None:
        """Maintain backward compatibility for legacy indicator kwargs/attrs."""
        if self.indicators is None:
            self.indicators = {}

        legacy = {
            "ema50": ema50,
            "ema200": ema200,
            "ema200_daily": ema200_daily,
            "atr": atr,
            "rsi": rsi,
            "adx": adx,
            "plus_di": plus_di,
            "minus_di": minus_di,
        }
        for key, value in legacy.items():
            if value is not None and key not in self.indicators:
                self.indicators[key] = float(value)

    def indicator(self, name: str, default: float = 0.0) -> float:
        """Typed helper for indicator lookup with default fallback."""
        return float(self.indicators.get(name, default))

    def __getattr__(self, name: str) -> float:
        """Backward-compatible dynamic access (e.g., bar.ema50)."""
        if name in self.indicators:
            return float(self.indicators[name])
        raise AttributeError(f"{self.__class__.__name__!s} has no attribute {name!r}")


@dataclass
class Position:
    """Current position state."""
    side: str  # "long" or "short"
    entry_price: float
    quantity: float
    stop_price: float
    entry_time: pd.Timestamp
