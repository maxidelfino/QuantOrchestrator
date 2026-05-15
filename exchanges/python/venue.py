"""Venue registry, adapter protocol, and factory."""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional, Protocol

import pandas as pd

from bots.python.core.config import ExchangeConfig
from exchanges.python.binance_futures import BinanceFutures
from exchanges.python.hyperliquid_perps import HyperliquidPerps


class Venue(Enum):
    HYPERLIQUID = "hyperliquid"
    BINANCE = "binance"
    EXTENDED = "extended"
    ZERONE = "01"
    RISEX = "risex"
    PARADEX = "paradex"
    LIGHTER = "lighter"

    @classmethod
    def from_string(cls, value: str) -> "Venue":
        for v in cls:
            if v.value == value.lower():
                return v
        raise ValueError(f"Unknown venue: '{value}'. Supported: {', '.join(v.value for v in cls)}")

    @property
    def is_implemented(self) -> bool:
        return self in (Venue.HYPERLIQUID, Venue.BINANCE)

    @property
    def docs_url(self) -> str:
        urls = {
            Venue.HYPERLIQUID: "https://hyperliquid.gitbook.io/hyperliquid-docs",
            Venue.BINANCE: "https://binance-docs.github.io/apidocs/futures/en/",
            Venue.EXTENDED: "https://docs.extended.exchange/",
            Venue.ZERONE: "https://docs.01.xyz/",
            Venue.RISEX: "https://docs.risechain.com/docs/risex",
            Venue.PARADEX: "https://docs.paradex.trade/home",
            Venue.LIGHTER: "https://docs.lighter.xyz/",
        }
        return urls.get(self, "")


class ExchangeAdapter(Protocol):
    supports_native_stop: bool

    async def connect(self) -> None: ...
    async def close(self) -> None: ...
    async def fetch_klines(self, symbol: str, timeframe: str, limit: int = 500) -> pd.DataFrame: ...
    async def fetch_daily_klines(self, symbol: str, limit: int = 500) -> pd.DataFrame: ...
    async def fetch_ticker(self, symbol: str) -> Dict[str, Any]: ...
    async def fetch_balance(self) -> Dict[str, Any]: ...
    async def fetch_position(self, symbol: str) -> Optional[Dict[str, Any]]: ...
    async def place_market_order(self, symbol: str, side: str, amount: float, reduce_only: bool = False) -> Dict[str, Any]: ...
    async def place_limit_order(self, symbol: str, side: str, amount: float, price: float, reduce_only: bool = False, post_only: bool = False) -> Dict[str, Any]: ...
    async def fetch_order(self, symbol: str, order_id: str) -> Dict[str, Any]: ...
    async def cancel_order(self, symbol: str, order_id: str) -> Dict[str, Any]: ...
    async def cancel_all_orders(self, symbol: str) -> List[Dict[str, Any]]: ...
    async def fetch_open_orders(self, symbol: str) -> List[Dict[str, Any]]: ...
    async def fetch_order_book(self, symbol: str, limit: int = 1) -> Dict[str, Any]: ...
    def get_equity(self, balance: Dict[str, Any]) -> float: ...


def create_exchange(config: ExchangeConfig) -> ExchangeAdapter:
    venue = config.venue.lower()
    if venue == "hyperliquid":
        return HyperliquidPerps(config)
    if venue == "binance":
        return BinanceFutures(config)

    planned = {
        "extended": ("Extended", "https://docs.extended.exchange/"),
        "01": ("01", "https://docs.01.xyz/"),
        "risex": ("RiseX", "https://docs.risechain.com/docs/risex"),
        "paradex": ("Paradex", "https://docs.paradex.trade/home"),
        "lighter": ("Lighter", "https://docs.lighter.xyz/"),
    }
    if venue in planned:
        name, docs = planned[venue]
        raise NotImplementedError(f"{name} adapter not yet implemented. See docs: {docs}")

    raise ValueError("Unknown venue: '{venue}'. Supported: hyperliquid, binance".format(venue=venue))
