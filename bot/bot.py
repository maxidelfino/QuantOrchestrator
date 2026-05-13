"""
Bot Orchestrator — Main trading loop

Coordinates:
1. Fetch market data
2. Compute indicators
3. Evaluate strategy signals
4. Check risk limits
5. Execute orders
6. Update state
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Optional

from bot.config import BotConfig
from bot.exchange import create_exchange
from bot.risk import RiskManager
from bot.state import BotState, StateManager
from bot.strategy import Bar, Position, Signal, V40Strategy

logger = logging.getLogger(__name__)


class TradingBot:
    """Main trading bot orchestrator."""

    def __init__(self, config: BotConfig):
        self.config = config
        self.exchange = create_exchange(config.exchange)
        self.strategy = V40Strategy(
            ema_fast=config.strategy.ema_fast,
            ema_slow=config.strategy.ema_slow,
            ema_regime_daily=config.strategy.ema_regime_daily,
            atr_period=config.strategy.atr_period,
            stop_atr_mult=config.strategy.stop_atr_mult,
        )
        self.risk = RiskManager(config.risk)
        self.state_mgr = StateManager(config.state_dir)

        # Runtime state
        self._position: Optional[Position] = None
        self._stop_order_id: Optional[str] = None
        self._running = False

    async def start(self) -> None:
        """Start the bot."""
        logger.info("Starting trading bot...")

        # Validate config
        errors = self.config.validate()
        if errors:
            logger.error(f"Configuration errors: {errors}")
            return

        # Connect to exchange
        await self.exchange.connect()

        # Connect to state manager
        self.state_mgr.connect()

        # Load previous state
        state = self.state_mgr.load()
        if state.current_position:
            self._position = Position(
                side=state.current_position["side"],
                entry_price=state.current_position["entry_price"],
                quantity=state.current_position["quantity"],
                stop_price=state.current_position["stop_price"],
                entry_time=datetime.fromisoformat(state.current_position["entry_time"]),
            )
            logger.info(f"Restored position: {self._position.side} @ {self._position.entry_price}")

        # Reconcile venue state on startup (fail-safe: venue is source of truth)
        await self._reconcile_startup_state(state)

        # Initialize risk manager
        balance = await self.exchange.fetch_balance()
        equity = self.exchange.get_equity(balance)
        self.risk.initialize(equity)
        logger.info(f"Initial equity: ${equity:.2f}")

        self._running = True
        state.is_running = True
        self.state_mgr.save(state)

        logger.info("Bot started successfully")

        # Main loop
        try:
            await self._main_loop()
        except KeyboardInterrupt:
            logger.info("Bot stopped by user")
        except Exception as e:
            logger.exception(f"Bot crashed: {e}")
            state.is_running = False
            state.error_count += 1
            state.last_error = str(e)
            self.state_mgr.save(state)
        finally:
            self._running = False
            await self.exchange.close()
            self.state_mgr.close()
            logger.info("Bot stopped")

    async def _main_loop(self) -> None:
        """Main trading loop."""
        logger.info(f"Main loop started (poll interval: {self.config.poll_interval_sec}s)")

        while self._running:
            try:
                await self._tick()
            except Exception as e:
                logger.error(f"Tick error: {e}")
                self.state_mgr.record_event("error", {"message": str(e)})

            await asyncio.sleep(self.config.poll_interval_sec)

    async def _tick(self) -> None:
        """Single tick of the trading loop."""
        symbol = self.config.exchange.symbol
        timeframe = self.config.exchange.timeframe

        # 1. Fetch market data
        logger.debug(f"Fetching {timeframe} klines for {symbol}...")
        df_4h = await self.exchange.fetch_klines(symbol, timeframe, limit=500)
        df_daily = await self.exchange.fetch_daily_klines(symbol, limit=500)

        # 2. Compute indicators
        df = self.strategy.compute_indicators(df_4h, df_daily)

        # 3. Get current bar
        current_bar = df.iloc[-1]
        bar = Bar(
            time=current_bar.name,
            open=current_bar["open"],
            high=current_bar["high"],
            low=current_bar["low"],
            close=current_bar["close"],
            volume=current_bar["volume"],
            ema50=current_bar["ema50"],
            ema200=current_bar["ema200"],
            ema200_daily=current_bar["ema200_daily"],
            atr=current_bar["atr"],
        )

        # Check if this is a new bar
        state = self.state_mgr.load()

        # For venues without reliable native stop support, enforce local stop every poll.
        if self._position is not None and not self.exchange.supports_native_stop:
            await self._enforce_local_stop(symbol)

        if state.last_bar_time == bar.time.isoformat():
            logger.debug(f"Same bar, skipping: {bar.time}")
            return

        logger.info(f"New bar: {bar.time} close={bar.close:.2f} ema50={bar.ema50:.2f} ema200={bar.ema200:.2f}")

        # 4. Update risk state
        balance = await self.exchange.fetch_balance()
        equity = self.exchange.get_equity(balance)
        self.risk.update(equity)

        # Check if trading is allowed
        can_trade, reason = self.risk.can_trade()
        if not can_trade:
            logger.warning(f"Trading halted: {reason}")
            # If we have a position, close it on halt
            if self._position:
                await self._close_position(symbol, "risk_halt")
            return

        # 5. Evaluate strategy
        signal = self.strategy.evaluate(bar, self._position)
        state.last_signal = signal.value
        state.last_bar_time = bar.time.isoformat()

        if signal != Signal.NONE:
            logger.info(f"Signal: {signal.value}")
            self.state_mgr.record_event("signal", {
                "signal": signal.value,
                "price": bar.close,
                "ema50": bar.ema50,
                "ema200": bar.ema200,
            })

        # 6. Execute
        await self._execute(signal, bar, equity, symbol)

        # 7. Save state
        state.current_position = asdict(self._position) if self._position else None
        self.state_mgr.save(state)

    async def _execute(self, signal: Signal, bar: Bar, equity: float, symbol: str) -> None:
        """Execute trading signal."""
        if signal == Signal.LONG and self._position is None:
            await self._open_position("long", bar, equity, symbol)

        elif signal == Signal.SHORT and self._position is None:
            await self._open_position("short", bar, equity, symbol)

        elif signal == Signal.CLOSE_LONG and self._position is not None:
            await self._close_position(symbol, "strategy_exit")

        elif signal == Signal.CLOSE_SHORT and self._position is not None:
            await self._close_position(symbol, "strategy_exit")

    async def _open_position(self, side: str, bar: Bar, equity: float, symbol: str) -> None:
        """Open a new position."""
        # Calculate stop loss
        stop_price = self.strategy.calc_stop_price(bar.close, bar.atr, side)

        # Calculate position size
        quantity = self.strategy.calc_position_size(equity, bar.close, stop_price)

        # Validate order size
        valid, reason = self.risk.validate_order_size(quantity, bar.close)
        if not valid:
            logger.warning(f"Order rejected: {reason}")
            return

        # Place market order
        order_side = "buy" if side == "long" else "sell"
        try:
            order = await self.exchange.place_market_order(symbol, order_side, quantity)
            fill_price = float(order.get("average", bar.close))

            # Place stop loss
            stop_side = "sell" if side == "long" else "buy"
            stop_order = await self.exchange.place_stop_order(
                symbol, stop_side, quantity, stop_price
            )
            self._stop_order_id = stop_order.get("id")

            # Record position
            self._position = Position(
                side=side,
                entry_price=fill_price,
                quantity=quantity,
                stop_price=stop_price,
                entry_time=bar.time,
            )

            logger.info(
                f"Position opened: {side} {quantity:.6f} {symbol} @ {fill_price:.2f} "
                f"stop={stop_price:.2f}"
            )
            self.state_mgr.record_event("open", {
                "side": side,
                "quantity": quantity,
                "entry_price": fill_price,
                "stop_price": stop_price,
            })

        except Exception as e:
            logger.error(f"Failed to open position: {e}")
            self.state_mgr.record_event("open_error", {"side": side, "error": str(e)})

    async def _close_position(self, symbol: str, reason: str) -> None:
        """Close current position."""
        if self._position is None:
            return

        try:
            # Cancel stop order first
            if self._stop_order_id:
                try:
                    await self.exchange.cancel_order(symbol, self._stop_order_id)
                except Exception:
                    pass  # May already be filled

            # Close position
            close_side = "sell" if self._position.side == "long" else "buy"
            order = await self.exchange.place_market_order(
                symbol, close_side, self._position.quantity, reduce_only=True
            )
            exit_price = float(order.get("average", 0))

            # Calculate PnL
            if self._position.side == "long":
                pnl = self._position.quantity * (exit_price - self._position.entry_price)
            else:
                pnl = self._position.quantity * (self._position.entry_price - exit_price)

            # Record trade
            self.state_mgr.record_trade(
                entry_time=self._position.entry_time.isoformat(),
                exit_time=datetime.utcnow().isoformat(),
                symbol=symbol,
                side=self._position.side,
                entry_price=self._position.entry_price,
                exit_price=exit_price,
                quantity=self._position.quantity,
                pnl=pnl,
                reason=reason,
            )

            logger.info(
                f"Position closed: {self._position.side} {symbol} "
                f"entry={self._position.entry_price:.2f} exit={exit_price:.2f} pnl={pnl:.2f}"
            )
            self.state_mgr.record_event("close", {
                "side": self._position.side,
                "exit_price": exit_price,
                "pnl": pnl,
                "reason": reason,
            })

            # Clear position
            self._position = None
            self._stop_order_id = None

        except Exception as e:
            logger.error(f"Failed to close position: {e}")
            self.state_mgr.record_event("close_error", {"error": str(e)})

    async def _enforce_local_stop(self, symbol: str) -> None:
        """Close immediately if local stop has been breached."""
        ticker = await self.exchange.fetch_ticker(symbol)
        mark = ticker.get("last") or ticker.get("mark") or ticker.get("close")
        if mark is None:
            logger.warning("Local stop check skipped: ticker has no price field")
            return

        price = float(mark)
        hit = (
            self._position.side == "long" and price <= self._position.stop_price
        ) or (
            self._position.side == "short" and price >= self._position.stop_price
        )
        if hit:
            logger.warning(
                "Local stop triggered: side=%s mark=%.2f stop=%.2f",
                self._position.side,
                price,
                self._position.stop_price,
            )
            await self._close_position(symbol, "local_stop_trigger")

    async def _reconcile_startup_state(self, state: BotState) -> None:
        """Reconcile persisted state with venue position/open orders.

        Wrapped in try/except: Hyperliquid testnet has known ccxt issues
        with fetch_positions (422 error). Bot continues with local state
        and will reconcile on next successful poll.
        """
        symbol = self.config.exchange.symbol
        try:
            venue_position = await self.exchange.fetch_position(symbol)
        except Exception as e:
            logger.warning("Could not fetch venue position (will retry next poll): %s", e)
            venue_position = None

        try:
            open_orders = await self.exchange.fetch_open_orders(symbol)
        except Exception as e:
            logger.warning("Could not fetch open orders (will retry next poll): %s", e)
            open_orders = []

        if venue_position and self._position is None:
            raise RuntimeError(
                "Venue has open BTC position but local state is empty. "
                "Failing closed for safety; reconcile manually before restart."
            )

        if not venue_position and self._position is not None:
            logger.warning("Local state had position but venue has none; clearing local position.")
            self._position = None
            self._stop_order_id = None

        # Track first known stop-like open order if present.
        if open_orders and self._stop_order_id is None:
            for order in open_orders:
                info = str(order.get("type", "")).lower()
                if "stop" in info:
                    self._stop_order_id = str(order.get("id"))
                    break

        state.current_position = asdict(self._position) if self._position else None
        self.state_mgr.save(state)


def asdict(obj) -> dict:
    """Simple dataclass to dict conversion."""
    from dataclasses import fields, is_dataclass
    if is_dataclass(obj):
        result = {}
        for f in fields(obj):
            val = getattr(obj, f.name)
            if isinstance(val, datetime):
                result[f.name] = val.isoformat()
            else:
                result[f.name] = val
        return result
    return {}
