from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import ccxt.async_support as ccxt_async
import pandas as pd

from bots.python.core.config import ExchangeConfig

logger = logging.getLogger(__name__)


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
