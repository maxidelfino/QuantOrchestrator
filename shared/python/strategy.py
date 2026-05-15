"""
Shared types — Signal enum, Bar, Position dataclasses.

Used by all strategies and the orchestrator.
"""

from __future__ import annotations

from dataclasses import dataclass
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
    ema50: float = 0.0
    ema200: float = 0.0
    ema200_daily: float = 0.0
    atr: float = 0.0
    # v48b / btc-momentum-1h indicators
    rsi: float = 50.0
    adx: float = 0.0
    plus_di: float = 0.0
    minus_di: float = 0.0


@dataclass
class Position:
    """Current position state."""
    side: str  # "long" or "short"
    entry_price: float
    quantity: float
    stop_price: float
    entry_time: pd.Timestamp
