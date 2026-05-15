from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import ccxt.async_support as ccxt_async
import pandas as pd

from shared.python.config import ExchangeConfig

logger = logging.getLogger(__name__)


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
        for key in ("USDC", "USD", "USDT"):
            if key in total and total[key] is not None:
                return float(total[key])
        info = balance.get("info", {})
        if isinstance(info, dict) and "accountValue" in info:
            return float(info["accountValue"])
        return 0.0
