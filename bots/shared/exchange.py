"""
Exchange adapters.

Supports:
- Binance Futures (existing path)
- Hyperliquid Perps (testnet-first path)

Future venues (placeholders — adapters not yet implemented):
- Extended: https://docs.extended.exchange/
- 01: https://docs.01.xyz/
- RiseX: https://docs.risechain.com/docs/risex
- Paradex: https://docs.paradex.trade/home
- Lighter: https://docs.lighter.xyz/
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any, Dict, List, Optional, Protocol

import ccxt.async_support as ccxt_async
import pandas as pd

from bots.shared.config import ExchangeConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Venue registry
# ---------------------------------------------------------------------------

class Venue(Enum):
    """Supported trading venues (CEX + DEX).

    Each venue requires its own adapter implementation.
    Currently implemented: HYPERLIQUID, BINANCE.

    Planned (docs linked for reference):
    - EXTENDED: https://docs.extended.exchange/
    - ZERONE:   https://docs.01.xyz/
    - RISEX:    https://docs.risechain.com/docs/risex
    - PARADEX:  https://docs.paradex.trade/home
    - LIGHTER:  https://docs.lighter.xyz/
    """
    HYPERLIQUID = "hyperliquid"
    BINANCE = "binance"

    # Planned DEX venues — adapters not yet implemented
    EXTENDED = "extended"      # https://docs.extended.exchange/
    ZERONE = "01"              # https://docs.01.xyz/
    RISEX = "risex"            # https://docs.risechain.com/docs/risex
    PARADEX = "paradex"        # https://docs.paradex.trade/home
    LIGHTER = "lighter"        # https://docs.lighter.xyz/

    @classmethod
    def from_string(cls, value: str) -> "Venue":
        """Parse venue from string (case-insensitive)."""
        for v in cls:
            if v.value == value.lower():
                return v
        raise ValueError(
            f"Unknown venue: '{value}'. "
            f"Supported: {', '.join(v.value for v in cls)}"
        )

    @property
    def is_implemented(self) -> bool:
        """Whether an adapter exists for this venue."""
        return self in (Venue.HYPERLIQUID, Venue.BINANCE)

    @property
    def docs_url(self) -> str:
        """Link to official venue documentation."""
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


# ---------------------------------------------------------------------------
# Adapter protocol
# ---------------------------------------------------------------------------

class ExchangeAdapter(Protocol):
    """Adapter contract used by TradingBot."""

    supports_native_stop: bool

    async def connect(self) -> None: ...
    async def close(self) -> None: ...
    async def fetch_klines(self, symbol: str, timeframe: str, limit: int = 500) -> pd.DataFrame: ...
    async def fetch_daily_klines(self, symbol: str, limit: int = 500) -> pd.DataFrame: ...
    async def fetch_ticker(self, symbol: str) -> Dict[str, Any]: ...
    async def fetch_balance(self) -> Dict[str, Any]: ...
    async def fetch_position(self, symbol: str) -> Optional[Dict[str, Any]]: ...
    async def place_market_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        reduce_only: bool = False,
    ) -> Dict[str, Any]: ...
    async def place_limit_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        price: float,
        reduce_only: bool = False,
        post_only: bool = False,
    ) -> Dict[str, Any]: ...
    async def fetch_order(self, symbol: str, order_id: str) -> Dict[str, Any]: ...
    async def cancel_order(self, symbol: str, order_id: str) -> Dict[str, Any]: ...
    async def cancel_all_orders(self, symbol: str) -> List[Dict[str, Any]]: ...
    async def fetch_open_orders(self, symbol: str) -> List[Dict[str, Any]]: ...
    async def fetch_order_book(self, symbol: str, limit: int = 1) -> Dict[str, Any]: ...
    def get_equity(self, balance: Dict[str, Any]) -> float: ...


# ---------------------------------------------------------------------------
# Binance Futures adapter
# ---------------------------------------------------------------------------

class BinanceFutures:
    """Async Binance Futures adapter."""

    supports_native_stop = True

    def __init__(self, config: ExchangeConfig):
        self.config = config
        self._exchange: Optional[ccxt_async.binance] = None

    async def connect(self) -> None:
        opts: Dict[str, Any] = {
            "enableRateLimit": True,
            "options": {"defaultType": "future"},
        }
        if self.config.testnet:
            opts["urls"] = {
                "api": {
                    "public": "https://testnet.binancefuture.com/fapi/v1",
                    "private": "https://testnet.binancefuture.com/fapi/v1",
                }
            }

        self._exchange = ccxt_async.binance({
            "apiKey": self.config.api_key,
            "secret": self.config.api_secret,
            **opts,
        })

        if self.config.testnet:
            self._exchange.set_sandbox_mode(True)

        if self.config.leverage > 1:
            await self._exchange.set_leverage(self.config.leverage, self.config.symbol)

        logger.info("Connected to Binance Futures (testnet=%s)", self.config.testnet)

    async def close(self) -> None:
        if self._exchange:
            await self._exchange.close()

    async def fetch_klines(self, symbol: str, timeframe: str, limit: int = 500) -> pd.DataFrame:
        ohlcv = await self._exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        df = pd.DataFrame(ohlcv, columns=["time", "open", "high", "low", "close", "volume"])
        df["time"] = pd.to_datetime(df["time"], unit="ms", utc=True)
        return df.set_index("time")

    async def fetch_daily_klines(self, symbol: str, limit: int = 500) -> pd.DataFrame:
        return await self.fetch_klines(symbol, "1d", limit=limit)

    async def fetch_ticker(self, symbol: str) -> Dict[str, Any]:
        return await self._exchange.fetch_ticker(symbol)

    async def fetch_balance(self) -> Dict[str, Any]:
        return await self._exchange.fetch_balance()

    async def fetch_position(self, symbol: str) -> Optional[Dict[str, Any]]:
        positions = await self._exchange.fetch_positions([symbol])
        for pos in positions:
            if pos["symbol"] == symbol and float(pos.get("contracts", 0)) != 0:
                return pos
        return None

    async def place_market_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        reduce_only: bool = False,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {}
        if reduce_only:
            params["reduceOnly"] = True
        order = await self._exchange.create_market_order(symbol, side, amount, params=params)
        logger.info("Binance market order: %s %.6f %s reduceOnly=%s", side, amount, symbol, reduce_only)
        return order

    async def place_limit_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        price: float,
        reduce_only: bool = False,
        post_only: bool = False,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {}
        if reduce_only:
            params["reduceOnly"] = True
        if post_only:
            params["postOnly"] = True
        order = await self._exchange.create_order(symbol, "LIMIT", side, amount, price, params=params)
        logger.info(
            "Binance limit order: %s %.6f %s @ %.2f reduceOnly=%s postOnly=%s",
            side, amount, symbol, price, reduce_only, post_only,
        )
        return order

    async def fetch_order(self, symbol: str, order_id: str) -> Dict[str, Any]:
        return await self._exchange.fetch_order(order_id, symbol)

    async def place_stop_order(self, symbol: str, side: str, amount: float, stop_price: float) -> Dict[str, Any]:
        params = {"stopPrice": stop_price, "reduceOnly": True}
        order = await self._exchange.create_order(symbol, "STOP_MARKET", side, amount, None, params)
        logger.info("Binance stop order: %s %.6f %s stop=%s", side, amount, symbol, stop_price)
        return order

    async def cancel_order(self, symbol: str, order_id: str) -> Dict[str, Any]:
        return await self._exchange.cancel_order(order_id, symbol)

    async def cancel_all_orders(self, symbol: str) -> List[Dict[str, Any]]:
        return await self._exchange.cancel_all_orders(symbol)

    async def fetch_open_orders(self, symbol: str) -> List[Dict[str, Any]]:
        return await self._exchange.fetch_open_orders(symbol)

    async def fetch_order_book(self, symbol: str, limit: int = 1) -> Dict[str, Any]:
        return await self._exchange.fetch_order_book(symbol, limit)

    def get_equity(self, balance: Dict[str, Any]) -> float:
        total = balance.get("total", {})
        if "USDT" in total:
            return float(total["USDT"])
        info = balance.get("info", {})
        return float(info.get("totalWalletBalance", 0))


# ---------------------------------------------------------------------------
# Hyperliquid Perps adapter
# ---------------------------------------------------------------------------

class HyperliquidPerps:
    """Hyperliquid perps adapter with testnet-first defaults.

    Native server-side stop support via ccxt is not reliable across all environments,
    so this adapter fails closed by refusing native stop placement and requiring local stop logic.

    Docs: https://hyperliquid.gitbook.io/hyperliquid-docs
    """

    supports_native_stop = False

    def __init__(self, config: ExchangeConfig):
        self.config = config
        self._exchange: Optional[ccxt_async.hyperliquid] = None

    async def connect(self) -> None:
        opts: Dict[str, Any] = {
            "enableRateLimit": True,
            "walletAddress": self.config.wallet_address,
            "privateKey": self.config.private_key,
        }
        self._exchange = ccxt_async.hyperliquid(opts)

        if self.config.testnet:
            self._exchange.set_sandbox_mode(True)
            testnet_url = "https://api.hyperliquid-testnet.xyz"
            self._exchange.urls["api"] = {
                "public": testnet_url,
                "private": testnet_url,
            }

        await self._exchange.load_markets()
        logger.info("Connected to Hyperliquid (testnet=%s)", self.config.testnet)

    async def close(self) -> None:
        if self._exchange:
            await self._exchange.close()

    async def fetch_klines(self, symbol: str, timeframe: str, limit: int = 500) -> pd.DataFrame:
        ohlcv = await self._exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        df = pd.DataFrame(ohlcv, columns=["time", "open", "high", "low", "close", "volume"])
        df["time"] = pd.to_datetime(df["time"], unit="ms", utc=True)
        return df.set_index("time")

    async def fetch_daily_klines(self, symbol: str, limit: int = 500) -> pd.DataFrame:
        return await self.fetch_klines(symbol, "1d", limit=limit)

    async def fetch_ticker(self, symbol: str) -> Dict[str, Any]:
        return await self._exchange.fetch_ticker(symbol)

    async def fetch_balance(self) -> Dict[str, Any]:
        return await self._exchange.fetch_balance()

    async def fetch_position(self, symbol: str) -> Optional[Dict[str, Any]]:
        positions = await self._exchange.fetch_positions([symbol])
        for pos in positions:
            contracts = float(pos.get("contracts") or pos.get("contractSize") or 0)
            if pos.get("symbol") == symbol and contracts != 0:
                return pos
        return None

    async def place_market_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        reduce_only: bool = False,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {"reduceOnly": reduce_only}
        order = await self._exchange.create_market_order(symbol, side, amount, params=params)
        logger.info("Hyperliquid market order: %s %.6f %s reduceOnly=%s", side, amount, symbol, reduce_only)
        return order

    async def place_limit_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        price: float,
        reduce_only: bool = False,
        post_only: bool = False,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {"reduceOnly": reduce_only}
        if post_only:
            params["postOnly"] = True
        order = await self._exchange.create_order(symbol, "LIMIT", side, amount, price, params=params)
        logger.info(
            "Hyperliquid limit order: %s %.6f %s @ %.2f reduceOnly=%s postOnly=%s",
            side, amount, symbol, price, reduce_only, post_only,
        )
        return order

    async def fetch_order(self, symbol: str, order_id: str) -> Dict[str, Any]:
        return await self._exchange.fetch_order(order_id, symbol)

    async def place_stop_order(self, symbol: str, side: str, amount: float, stop_price: float) -> Dict[str, Any]:
        raise RuntimeError(
            "Hyperliquid native stop via ccxt is disabled for safety; use local stop trigger logic."
        )

    async def cancel_order(self, symbol: str, order_id: str) -> Dict[str, Any]:
        return await self._exchange.cancel_order(order_id, symbol)

    async def cancel_all_orders(self, symbol: str) -> List[Dict[str, Any]]:
        return await self._exchange.cancel_all_orders(symbol)

    async def fetch_open_orders(self, symbol: str) -> List[Dict[str, Any]]:
        return await self._exchange.fetch_open_orders(symbol)

    async def fetch_order_book(self, symbol: str, limit: int = 1) -> Dict[str, Any]:
        return await self._exchange.fetch_order_book(symbol, limit)

    def get_equity(self, balance: Dict[str, Any]) -> float:
        total = balance.get("total", {})
        # Hyperliquid testnet margin is commonly USDC.
        for key in ("USDC", "USD", "USDT"):
            if key in total and total[key] is not None:
                return float(total[key])
        info = balance.get("info", {})
        if isinstance(info, dict) and "accountValue" in info:
            return float(info["accountValue"])
        return 0.0


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_exchange(config: ExchangeConfig) -> ExchangeAdapter:
    """Create exchange adapter based on venue config.

    Raises NotImplementedError for venues without an adapter yet.
    """
    venue = config.venue.lower()

    if venue == "hyperliquid":
        return HyperliquidPerps(config)
    if venue == "binance":
        return BinanceFutures(config)

    # Planned venues — not yet implemented
    planned = {
        "extended": ("Extended", "https://docs.extended.exchange/"),
        "01": ("01", "https://docs.01.xyz/"),
        "risex": ("RiseX", "https://docs.risechain.com/docs/risex"),
        "paradex": ("Paradex", "https://docs.paradex.trade/home"),
        "lighter": ("Lighter", "https://docs.lighter.xyz/"),
    }

    if venue in planned:
        name, docs = planned[venue]
        raise NotImplementedError(
            f"{name} adapter not yet implemented. "
            f"See docs: {docs}"
        )

    raise ValueError(
        f"Unknown venue: '{venue}'. "
        f"Supported: hyperliquid, binance"
    )
