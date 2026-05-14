"""
Bot Orchestrator — Multi-strategy trading loop

Coordinates:
1. Fetch market data per strategy timeframe
2. Compute indicators per strategy
3. Evaluate strategy signals independently
4. Check shared risk limits
5. Execute orders per strategy (limit entries with TTL, market stops)
6. Update per-strategy state
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import fields, is_dataclass
from datetime import datetime
from typing import Any, Dict, Optional

from bots.shared.config import BotConfig
from bots.shared.exchange import create_exchange
from bots.shared.risk import RiskManager
from bots.shared.state import BotState, StateManager
from bots.shared.strategy import Bar, Position, Signal

logger = logging.getLogger(__name__)


class StrategyContext:
    """Per-strategy runtime context."""

    def __init__(self, name: str, timeframe: str):
        self.name = name
        self.timeframe = timeframe
        self.position: Optional[Position] = None
        self.stop_order_id: Optional[str] = None
        self.warmup_done = False
        # Pending limit order tracking (in-memory, restored from state on startup)
        self.pending_limit_order: Optional[Dict[str, Any]] = None


class TradingBot:
    """Multi-strategy trading bot orchestrator."""

    def __init__(self, config: BotConfig, strategies: Dict[str, Any]):
        """
        Args:
            config: Full bot configuration.
            strategies: Dict mapping strategy name to (instance, timeframe) tuples.
                        e.g. {"btc-trend-4h": (v40_instance, "4h"), ...}
        """
        self.config = config
        self.exchange = create_exchange(config.exchange)
        self.risk = RiskManager(config.risk)
        self.state_mgr = StateManager(config.state_dir)

        # Strategy contexts — keyed by name
        self._strategies: Dict[str, StrategyContext] = {}
        # Strategy engines — keyed by name
        self._engines: Dict[str, Any] = {}

        for name, (engine, timeframe) in strategies.items():
            self._engines[name] = engine
            ctx = StrategyContext(name, timeframe)
            self._strategies[name] = ctx
            logger.info(f"Loaded strategy: {name} (timeframe={timeframe})")

        self._running = False

    async def start(self) -> None:
        """Start the bot."""
        logger.info("Starting trading bot...")

        errors = self.config.validate()
        if errors:
            logger.error(f"Configuration errors: {errors}")
            return

        await self.exchange.connect()
        self.state_mgr.connect()

        # Venue name for logging
        venue_name = self.config.exchange.venue
        logger.info(f"Connected to {venue_name} (testnet={self.config.exchange.testnet})")

        # Load and restore per-strategy positions
        state = self.state_mgr.load()
        for name, ctx in self._strategies.items():
            pos_data = state.positions.get(name)
            if pos_data:
                ctx.position = Position(
                    side=pos_data["side"],
                    entry_price=pos_data["entry_price"],
                    quantity=pos_data["quantity"],
                    stop_price=pos_data["stop_price"],
                    entry_time=datetime.fromisoformat(pos_data["entry_time"]),
                )
                logger.info(f"[{name}] Restored position: {ctx.position.side} @ {ctx.position.entry_price}")

            # Restore pending limit orders from state
            pending = state.pending_orders.get(name)
            if pending:
                ctx.pending_limit_order = pending
                logger.info(
                    f"[{name}] Restored pending limit order: {pending.get('order_id')} "
                    f"@ {pending.get('price')}"
                )

        # Reconcile venue state on startup (positions + open orders)
        await self._reconcile_startup_state(state)

        # Initialize risk manager
        balance = await self.exchange.fetch_balance()
        equity = self.exchange.get_equity(balance)
        self.risk.initialize(equity)
        logger.info(f"Initial equity: ${equity:.2f}")

        self._running = True
        state.is_running = True
        self.state_mgr.save(state)

        strategy_names = ", ".join(self._strategies.keys())
        logger.info(f"Bot started with strategies: {strategy_names}")

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
        """Main trading loop — evaluates all strategies each tick."""
        logger.info(f"Main loop started (poll interval: {self.config.poll_interval_sec}s)")

        while self._running:
            try:
                await self._tick()
            except Exception as e:
                logger.error(f"Tick error: {e}")
                self.state_mgr.record_event("error", {"message": str(e)})

            await asyncio.sleep(self.config.poll_interval_sec)

    async def _tick(self) -> None:
        """Single tick: check pending orders, then evaluate all strategies."""
        symbol = self.config.exchange.symbol

        # Update shared risk state
        balance = await self.exchange.fetch_balance()
        equity = self.exchange.get_equity(balance)
        self.risk.update(equity)

        can_trade, reason = self.risk.can_trade()
        if not can_trade:
            logger.warning(f"Trading halted: {reason}")
            # Close all positions on halt
            for name, ctx in self._strategies.items():
                if ctx.position:
                    await self._close_position(name, symbol, "risk_halt")
            return

        state = self.state_mgr.load()

        # Check pending limit orders BEFORE evaluating strategies
        await self._check_pending_orders(symbol, state)

        # Evaluate each strategy independently
        for name, ctx in self._strategies.items():
            try:
                await self._evaluate_strategy(name, ctx, symbol, equity, state)
            except Exception as e:
                logger.error(f"[{name}] Tick error: {e}")
                self.state_mgr.record_event("error", {"strategy": name, "message": str(e)})

        # Save consolidated state
        self._sync_state_to_persisted(state)
        self.state_mgr.save(state)

    async def _evaluate_strategy(
        self, name: str, ctx: StrategyContext, symbol: str, equity: float, state: BotState
    ) -> None:
        """Fetch data, evaluate signals, and execute for one strategy."""
        timeframe = ctx.timeframe
        engine = self._engines[name]

        # 1. Fetch klines
        logger.debug(f"[{name}] Fetching {timeframe} klines for {symbol}...")
        df = await self.exchange.fetch_klines(symbol, timeframe, limit=500)
        df_daily = await self.exchange.fetch_daily_klines(symbol, limit=500)

        # 2. Compute indicators
        df_ind = engine.compute_indicators(df, df_daily)

        # Update RSI state for momentum strategy if applicable
        if hasattr(engine, "update_rsi_state"):
            current_rsi = df_ind.iloc[-1].get("rsi", 50.0)
            engine.update_rsi_state(current_rsi)

        # 3. Check for new bar
        current_bar = df_ind.iloc[-1]
        bar_time = current_bar.name.isoformat()
        last_bar = state.last_bar_time.get(name, "")

        # Enforce local stop for Hyperliquid (no native stops)
        if ctx.position is not None and not self.exchange.supports_native_stop:
            await self._enforce_local_stop(name, ctx, symbol)

        if last_bar == bar_time:
            logger.debug(f"[{name}] Same bar, skipping: {current_bar.name}")
            return

        logger.info(
            f"[{name}] New {timeframe} bar: {current_bar.name} close={current_bar['close']:.2f}"
        )

        # 4. Build Bar object
        bar = Bar(
            time=current_bar.name,
            open=current_bar["open"],
            high=current_bar["high"],
            low=current_bar["low"],
            close=current_bar["close"],
            volume=current_bar["volume"],
            ema50=current_bar.get("ema50", 0.0),
            ema200=current_bar.get("ema200", 0.0),
            ema200_daily=current_bar.get("ema200_daily", 0.0),
            atr=current_bar.get("atr", 0.0),
            rsi=current_bar.get("rsi", 50.0),
            adx=current_bar.get("adx", 0.0),
            plus_di=current_bar.get("plus_di", 0.0),
            minus_di=current_bar.get("minus_di", 0.0),
        )

        # 5. Evaluate strategy
        signal = engine.evaluate(bar, ctx.position)

        state.last_bar_time[name] = bar_time
        state.last_signal = signal.value

        if signal != Signal.NONE:
            logger.info(f"[{name}] Signal: {signal.value}")
            self.state_mgr.record_event("signal", {
                "strategy": name,
                "signal": signal.value,
                "price": bar.close,
            })

        # 6. Execute
        await self._execute(name, ctx, signal, bar, equity, symbol)

    async def _execute(
        self, name: str, ctx: StrategyContext, signal: Signal,
        bar: Bar, equity: float, symbol: str
    ) -> None:
        """Execute trading signal for a specific strategy."""
        engine = self._engines[name]

        if signal == Signal.LONG and ctx.position is None:
            # Skip entry if we already have a pending limit order for this strategy
            if ctx.pending_limit_order is not None:
                logger.info(f"[{name}] Signal LONG but pending limit order exists, skipping")
                return
            await self._open_position(name, ctx, "long", bar, equity, symbol, engine)

        elif signal == Signal.SHORT and ctx.position is None:
            if ctx.pending_limit_order is not None:
                logger.info(f"[{name}] Signal SHORT but pending limit order exists, skipping")
                return
            await self._open_position(name, ctx, "short", bar, equity, symbol, engine)

        elif signal in (Signal.CLOSE_LONG, Signal.CLOSE_SHORT) and ctx.position is not None:
            await self._close_position(name, symbol, "strategy_exit")

    async def _open_position(
        self, name: str, ctx: StrategyContext, side: str,
        bar: Bar, equity: float, symbol: str, engine: Any
    ) -> None:
        """Open a new position for a strategy using limit or market order."""
        exec_cfg = self.config.execution
        stop_price = engine.calc_stop_price(bar.close, bar.atr, side)
        quantity = engine.calc_position_size(equity, bar.close, stop_price)

        valid, reason = self.risk.validate_order_size(quantity, bar.close)
        if not valid:
            logger.warning(f"[{name}] Order rejected: {reason}")
            return

        order_side = "buy" if side == "long" else "sell"

        # --- Limit order entry ---
        if exec_cfg.entry_order_type == "limit":
            try:
                limit_price = self._calc_limit_price(bar, side)
                order = await self.exchange.place_limit_order(
                    symbol, order_side, quantity, limit_price,
                    reduce_only=False,
                    post_only=exec_cfg.post_only,
                )
                order_id = order.get("id")
                order_status = order.get("status", "open")

                # If the order filled immediately (e.g., price crossed), treat as market
                if order_status == "closed":
                    fill_price = float(order.get("average", limit_price))
                    ctx.position = Position(
                        side=side,
                        entry_price=fill_price,
                        quantity=quantity,
                        stop_price=stop_price,
                        entry_time=bar.time,
                    )
                    logger.info(
                        f"[{name}] Limit filled immediately: {side} {quantity:.6f} {symbol} "
                        f"@ {fill_price:.2f} stop={stop_price:.2f}"
                    )
                    self.state_mgr.record_event("open", {
                        "strategy": name,
                        "side": side,
                        "quantity": quantity,
                        "entry_price": fill_price,
                        "stop_price": stop_price,
                        "order_type": "limit_immediate",
                    })
                else:
                    # Track pending limit order
                    bar_ts = bar.time.isoformat() if hasattr(bar.time, 'isoformat') else str(bar.time)
                    ctx.pending_limit_order = {
                        "order_id": order_id,
                        "side": order_side,
                        "quantity": quantity,
                        "price": limit_price,
                        "strategy_side": side,
                        "stop_price": stop_price,
                        "bar_placed": bar_ts,
                        "entry_time_iso": bar_ts,
                    }
                    logger.info(
                        f"[{name}] Limit placed at {limit_price:.2f} ({order_side}), "
                        f"waiting for fill (TTL={exec_cfg.entry_ttl_bars} bars)"
                    )
                    self.state_mgr.record_event("limit_placed", {
                        "strategy": name,
                        "side": side,
                        "quantity": quantity,
                        "limit_price": limit_price,
                        "order_id": order_id,
                    })
                return

            except Exception as e:
                logger.error(f"[{name}] Limit order failed, falling back to market: {e}")
                self.state_mgr.record_event("limit_error", {
                    "strategy": name, "side": side, "error": str(e),
                })
                # Fall through to market order

        # --- Market order entry (default or fallback) ---
        try:
            order = await self.exchange.place_market_order(symbol, order_side, quantity)
            fill_price = float(order.get("average", bar.close))

            # Hyperliquid: no native stop, rely on local enforcement
            if self.exchange.supports_native_stop:
                stop_side = "sell" if side == "long" else "buy"
                stop_order = await self.exchange.place_stop_order(
                    symbol, stop_side, quantity, stop_price
                )
                ctx.stop_order_id = stop_order.get("id")

            ctx.position = Position(
                side=side,
                entry_price=fill_price,
                quantity=quantity,
                stop_price=stop_price,
                entry_time=bar.time,
            )

            logger.info(
                f"[{name}] Position opened (market): {side} {quantity:.6f} {symbol} @ {fill_price:.2f} "
                f"stop={stop_price:.2f}"
            )
            self.state_mgr.record_event("open", {
                "strategy": name,
                "side": side,
                "quantity": quantity,
                "entry_price": fill_price,
                "stop_price": stop_price,
                "order_type": "market",
            })

        except Exception as e:
            logger.error(f"[{name}] Failed to open position: {e}")
            self.state_mgr.record_event("open_error", {"strategy": name, "side": side, "error": str(e)})

    async def _close_position(self, name: str, symbol: str, reason: str) -> None:
        """Close position for a strategy.

        Uses market order for stop-loss exits (emergency).
        Uses limit order for planned exits (strategy_exit, trailing_stop).
        """
        ctx = self._strategies[name]
        if ctx.position is None:
            return

        exec_cfg = self.config.execution
        close_side = "sell" if ctx.position.side == "long" else "buy"
        quantity = ctx.position.quantity

        # Determine order type based on exit reason
        is_stop_exit = reason in ("local_stop_trigger", "risk_halt")
        use_market = is_stop_exit or exec_cfg.exit_stop_type == "market"

        # Cancel any pending entry limit order for this strategy
        if ctx.pending_limit_order:
            await self._cancel_pending_order(name, symbol)

        try:
            # Cancel stop order if exists
            if ctx.stop_order_id:
                try:
                    await self.exchange.cancel_order(symbol, ctx.stop_order_id)
                except Exception:
                    pass

            if use_market:
                # Market exit — immediate execution
                order = await self.exchange.place_market_order(
                    symbol, close_side, quantity, reduce_only=True
                )
                exit_price = float(order.get("average", 0))
                order_type_label = "market"
            else:
                # Limit exit — planned close
                # Get current price for limit placement
                ticker = await self.exchange.fetch_ticker(symbol)
                current_price = float(ticker.get("last") or ticker.get("close") or 0)
                if current_price == 0:
                    # Fallback to market if we can't get price
                    logger.warning(f"[{name}] No price for limit exit, using market")
                    order = await self.exchange.place_market_order(
                        symbol, close_side, quantity, reduce_only=True
                    )
                    exit_price = float(order.get("average", 0))
                    order_type_label = "market_fallback"
                else:
                    # Place limit slightly better than current price
                    offset = exec_cfg.limit_price_offset
                    limit_price = current_price * (1 + offset) if close_side == "sell" else current_price * (1 - offset)
                    order = await self.exchange.place_limit_order(
                        symbol, close_side, quantity, limit_price,
                        reduce_only=True,
                        post_only=exec_cfg.post_only,
                    )
                    exit_price = float(order.get("average", limit_price))
                    order_type_label = "limit"

            if ctx.position.side == "long":
                pnl = ctx.position.quantity * (exit_price - ctx.position.entry_price)
            else:
                pnl = ctx.position.quantity * (ctx.position.entry_price - exit_price)

            self.state_mgr.record_trade(
                entry_time=ctx.position.entry_time.isoformat(),
                exit_time=datetime.utcnow().isoformat(),
                symbol=symbol,
                side=ctx.position.side,
                entry_price=ctx.position.entry_price,
                exit_price=exit_price,
                quantity=ctx.position.quantity,
                pnl=pnl,
                reason=reason,
            )

            logger.info(
                f"[{name}] Position closed ({order_type_label}): {ctx.position.side} {symbol} "
                f"entry={ctx.position.entry_price:.2f} exit={exit_price:.2f} pnl={pnl:.2f} reason={reason}"
            )
            self.state_mgr.record_event("close", {
                "strategy": name,
                "side": ctx.position.side,
                "exit_price": exit_price,
                "pnl": pnl,
                "reason": reason,
                "order_type": order_type_label,
            })

            ctx.position = None
            ctx.stop_order_id = None

        except Exception as e:
            logger.error(f"[{name}] Failed to close position: {e}")
            self.state_mgr.record_event("close_error", {"strategy": name, "error": str(e)})

    async def _check_pending_orders(self, symbol: str, state: BotState) -> None:
        """Check all pending limit orders: fill status and TTL expiry.

        Called every tick before strategy evaluation.
        """
        exec_cfg = self.config.execution

        for name, ctx in self._strategies.items():
            pending = ctx.pending_limit_order
            if pending is None:
                continue

            order_id = pending["order_id"]

            try:
                # Poll order status from venue
                order = await self.exchange.fetch_order(symbol, order_id)
                status = order.get("status", "open")

                if status == "closed":
                    # Order filled — create position
                    fill_price = float(order.get("average", pending["price"]))
                    quantity = float(order.get("filled", pending["quantity"]))
                    ctx.position = Position(
                        side=pending["strategy_side"],
                        entry_price=fill_price,
                        quantity=quantity,
                        stop_price=pending["stop_price"],
                        entry_time=datetime.fromisoformat(pending["entry_time_iso"]),
                    )
                    ctx.pending_limit_order = None
                    logger.info(
                        f"[{name}] Limit filled: {pending['strategy_side']} {quantity:.6f} "
                        f"@ {fill_price:.2f}"
                    )
                    self.state_mgr.record_event("limit_filled", {
                        "strategy": name,
                        "side": pending["strategy_side"],
                        "fill_price": fill_price,
                        "quantity": quantity,
                        "order_id": order_id,
                    })
                    continue

                elif status in ("canceled", "expired", "rejected"):
                    # Order was cancelled by venue or expired — fallback to market
                    logger.warning(
                        f"[{name}] Limit order {order_id} status={status}, entering at market"
                    )
                    self.state_mgr.record_event("limit_expired", {
                        "strategy": name, "order_id": order_id, "status": status,
                    })
                    ctx.pending_limit_order = None
                    await self._fallback_to_market(name, symbol, pending)
                    continue

                # Status is "open" — check TTL
                bar_placed = pending.get("bar_placed", "")
                current_bar = state.last_bar_time.get(name, "")
                bars_elapsed = self._bars_since(bar_placed, ctx.timeframe, current_bar)

                if bars_elapsed >= exec_cfg.entry_ttl_bars:
                    # TTL expired — cancel and fallback
                    logger.info(
                        f"[{name}] Limit TTL expired ({bars_elapsed} bars >= {exec_cfg.entry_ttl_bars}), "
                        f"canceling and entering at market"
                    )
                    self.state_mgr.record_event("limit_ttl_expired", {
                        "strategy": name,
                        "order_id": order_id,
                        "bars_elapsed": bars_elapsed,
                        "ttl_bars": exec_cfg.entry_ttl_bars,
                    })
                    await self._cancel_pending_order(name, symbol)
                    ctx.pending_limit_order = None
                    await self._fallback_to_market(name, symbol, pending)

            except Exception as e:
                # If we can't fetch order status, log and keep pending
                # On next tick we'll retry. If the issue persists, TTL will eventually expire.
                logger.warning(f"[{name}] Could not check order {order_id}: {e}")

    async def _cancel_pending_order(self, name: str, symbol: str) -> None:
        """Cancel a pending limit order for a strategy."""
        ctx = self._strategies[name]
        pending = ctx.pending_limit_order
        if pending is None:
            return

        try:
            await self.exchange.cancel_order(symbol, pending["order_id"])
            logger.info(f"[{name}] Canceled pending limit order {pending['order_id']}")
            self.state_mgr.record_event("limit_canceled", {
                "strategy": name,
                "order_id": pending["order_id"],
            })
        except Exception as e:
            logger.warning(f"[{name}] Failed to cancel order {pending['order_id']}: {e}")

    async def _fallback_to_market(self, name: str, symbol: str, pending: Dict[str, Any]) -> None:
        """Enter position at market after limit order expired or failed."""
        ctx = self._strategies[name]
        side = pending["strategy_side"]
        order_side = pending["side"]
        quantity = pending["quantity"]
        stop_price = pending["stop_price"]

        try:
            order = await self.exchange.place_market_order(symbol, order_side, quantity)
            fill_price = float(order.get("average", pending["price"]))

            ctx.position = Position(
                side=side,
                entry_price=fill_price,
                quantity=quantity,
                stop_price=stop_price,
                entry_time=datetime.utcnow(),
            )

            logger.info(
                f"[{name}] Market fallback entry: {side} {quantity:.6f} {symbol} @ {fill_price:.2f}"
            )
            self.state_mgr.record_event("open", {
                "strategy": name,
                "side": side,
                "quantity": quantity,
                "entry_price": fill_price,
                "stop_price": stop_price,
                "order_type": "market_fallback",
            })
        except Exception as e:
            logger.error(f"[{name}] Market fallback entry failed: {e}")
            self.state_mgr.record_event("open_error", {
                "strategy": name, "side": side, "error": str(e), "fallback": True,
            })

    async def _enforce_local_stop(self, name: str, ctx: StrategyContext, symbol: str) -> None:
        """Close immediately if local stop has been breached."""
        ticker = await self.exchange.fetch_ticker(symbol)
        mark = ticker.get("last") or ticker.get("mark") or ticker.get("close")
        if mark is None:
            logger.warning(f"[{name}] Local stop check skipped: ticker has no price field")
            return

        price = float(mark)
        hit = (
            ctx.position.side == "long" and price <= ctx.position.stop_price
        ) or (
            ctx.position.side == "short" and price >= ctx.position.stop_price
        )
        if hit:
            logger.warning(
                f"[{name}] Local stop triggered: side={ctx.position.side} "
                f"mark={price:.2f} stop={ctx.position.stop_price:.2f}"
            )
            await self._close_position(name, symbol, "local_stop_trigger")

    async def _reconcile_startup_state(self, state: BotState) -> None:
        """Reconcile persisted state with venue position/open orders."""
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

        # Check if venue has positions but local state doesn't
        has_local_positions = any(ctx.position for ctx in self._strategies.values())
        if venue_position and not has_local_positions:
            raise RuntimeError(
                "Venue has open position but local state is empty. "
                "Failing closed for safety; reconcile manually before restart."
            )

        # Clear local positions that don't exist on venue
        if not venue_position and has_local_positions:
            logger.warning("Local state had positions but venue has none; clearing.")
            for ctx in self._strategies.values():
                ctx.position = None
                ctx.stop_order_id = None
                ctx.pending_limit_order = None

        # Reconcile pending limit orders: check if they still exist on venue
        for name, ctx in self._strategies.items():
            if ctx.pending_limit_order:
                order_id = ctx.pending_limit_order.get("order_id")
                found_on_venue = False
                for order in open_orders:
                    if str(order.get("id")) == str(order_id):
                        found_on_venue = True
                        break

                if not found_on_venue:
                    # Order no longer exists on venue — clear it
                    logger.warning(
                        f"[{name}] Pending order {order_id} not found on venue; clearing."
                    )
                    ctx.pending_limit_order = None

        # Track first known stop-like open order
        if open_orders:
            for name, ctx in self._strategies.items():
                if ctx.stop_order_id is None:
                    for order in open_orders:
                        info = str(order.get("type", "")).lower()
                        if "stop" in info:
                            ctx.stop_order_id = str(order.get("id"))
                            break

        self._sync_state_to_persisted(state)
        self.state_mgr.save(state)

    def _sync_state_to_persisted(self, state: BotState) -> None:
        """Copy runtime strategy positions and pending orders into persisted state."""
        for name, ctx in self._strategies.items():
            if ctx.position:
                state.positions[name] = asdict(ctx.position)
            elif name in state.positions:
                del state.positions[name]

            if ctx.pending_limit_order:
                state.pending_orders[name] = ctx.pending_limit_order
            elif name in state.pending_orders:
                del state.pending_orders[name]

    def _calc_limit_price(self, bar: Bar, side: str) -> float:
        """Calculate limit price slightly better than the signal bar's close.

        For buys: place at close - offset (bid side, better price)
        For sells: place at close + offset (ask side, better price)
        """
        offset = self.config.execution.limit_price_offset
        if side == "long":
            return bar.close * (1 - offset)
        else:
            return bar.close * (1 + offset)

    def _bars_since(self, bar_placed: str, timeframe: str, current_bar: str) -> int:
        """Estimate bars elapsed between two bar timestamps.

        Uses ISO timestamp comparison. Returns 0 if timestamps can't be parsed.
        """
        if not bar_placed or not current_bar:
            return 0
        try:
            placed_dt = datetime.fromisoformat(bar_placed.replace("Z", "+00:00"))
            current_dt = datetime.fromisoformat(current_bar.replace("Z", "+00:00"))
            diff = current_dt - placed_dt
            # Convert timeframe to hours
            tf_hours = self._timeframe_to_hours(timeframe)
            if tf_hours == 0:
                return 0
            return int(diff.total_seconds() / (tf_hours * 3600))
        except (ValueError, AttributeError):
            return 0

    @staticmethod
    def _timeframe_to_hours(timeframe: str) -> float:
        """Convert timeframe string to hours."""
        tf = timeframe.lower()
        if tf.endswith("m"):
            return int(tf[:-1]) / 60
        elif tf.endswith("h"):
            return int(tf[:-1])
        elif tf.endswith("d"):
            return int(tf[:-1]) * 24
        elif tf.endswith("w"):
            return int(tf[:-1]) * 24 * 7
        return 0


def asdict(obj) -> dict:
    """Simple dataclass to dict conversion."""
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
