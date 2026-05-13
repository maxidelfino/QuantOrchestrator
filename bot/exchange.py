"""
Binance Futures Exchange Adapter

Handles:
- Market data (klines, ticker, balance)
- Order management (place, cancel, query)
- Position management
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import ccxt.async_support as ccxt_async
import pandas as pd

from bot.config import ExchangeConfig

logger = logging.getLogger(__name__)


class BinanceFutures:
    """Async Binance Futures adapter."""

    def __init__(self, config: ExchangeConfig):
        self.config = config
        self._exchange: Optional[ccxt_async.binance] = None

    async def connect(self) -> None:
        """Initialize exchange connection."""
        opts: Dict[str, Any] = {
            "enableRateLimit": True,
            "options": {
                "defaultType": "future",
            },
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

        # Set leverage
        if self.config.leverage > 1:
            await self._exchange.set_leverage(
                self.config.leverage,
                self.config.symbol,
            )

        logger.info(f"Connected to Binance Futures (testnet={self.config.testnet})")

    async def close(self) -> None:
        """Close exchange connection."""
        if self._exchange:
            await self._exchange.close()

    # ── Market Data ──────────────────────────────────────────────

    async def fetch_klines(self, symbol: str, timeframe: str, limit: int = 500) -> pd.DataFrame:
        """Fetch OHLCV klines as DataFrame."""
        ohlcv = await self._exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        df = pd.DataFrame(ohlcv, columns=["time", "open", "high", "low", "close", "volume"])
        df["time"] = pd.to_datetime(df["time"], unit="ms", utc=True)
        df = df.set_index("time")
        return df

    async def fetch_daily_klines(self, symbol: str, limit: int = 500) -> pd.DataFrame:
        """Fetch daily klines for regime filter."""
        return await self.fetch_klines(symbol, "1d", limit=limit)

    async def fetch_ticker(self, symbol: str) -> Dict[str, Any]:
        """Fetch current ticker."""
        return await self._exchange.fetch_ticker(symbol)

    async def fetch_balance(self) -> Dict[str, Any]:
        """Fetch account balance."""
        balance = await self._exchange.fetch_balance()
        return balance.get("info", {})

    # ── Position Management ──────────────────────────────────────

    async def fetch_position(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Fetch current position for a symbol."""
        positions = await self._exchange.fetch_positions([symbol])
        for pos in positions:
            if pos["symbol"] == symbol and float(pos.get("contracts", 0)) != 0:
                return pos
        return None

    async def close_position(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Close entire position for a symbol."""
        try:
            order = await self._exchange.close_position(symbol)
            logger.info(f"Position closed: {symbol}")
            return order
        except Exception as e:
            logger.error(f"Failed to close position {symbol}: {e}")
            raise

    # ── Order Management ─────────────────────────────────────────

    async def place_market_order(
        self,
        symbol: str,
        side: str,  # "buy" or "sell"
        amount: float,
    ) -> Dict[str, Any]:
        """Place a market order."""
        order = await self._exchange.create_market_order(symbol, side, amount)
        logger.info(f"Market order: {side} {amount} {symbol} @ {order.get('average', 'N/A')}")
        return order

    async def place_stop_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        stop_price: float,
    ) -> Dict[str, Any]:
        """Place a stop-market order."""
        params = {
            "stopPrice": stop_price,
            "reduceOnly": True,
        }
        order = await self._exchange.create_order(
            symbol,
            "STOP_MARKET",
            side,
            amount,
            None,  # No price for stop market
            params,
        )
        logger.info(f"Stop order: {side} {amount} {symbol} stop={stop_price}")
        return order

    async def cancel_order(self, symbol: str, order_id: str) -> Dict[str, Any]:
        """Cancel an order."""
        return await self._exchange.cancel_order(order_id, symbol)

    async def cancel_all_orders(self, symbol: str) -> List[Dict[str, Any]]:
        """Cancel all open orders for a symbol."""
        return await self._exchange.cancel_all_orders(symbol)

    async def fetch_open_orders(self, symbol: str) -> List[Dict[str, Any]]:
        """Fetch all open orders for a symbol."""
        return await self._exchange.fetch_open_orders(symbol)

    # ── Helpers ──────────────────────────────────────────────────

    def get_equity(self, balance: Dict[str, Any]) -> float:
        """Extract total equity from balance response."""
        info = balance.get("info", {})
        return float(info.get("totalWalletBalance", 0))

    def get_available_balance(self, balance: Dict[str, Any]) -> float:
        """Extract available balance."""
        info = balance.get("info", {})
        return float(info.get("availableBalance", 0))
