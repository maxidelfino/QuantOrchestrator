"""
State Manager — SQLite persistence

Persists:
- Current positions (per-strategy)
- Trade history
- Bot state (running/halted)
- Last processed bar timestamp (per-strategy)
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class BotState:
    """Persisted bot state."""
    is_running: bool = False
    # Per-strategy last bar time: {"btc-trend-4h": "...", "btc-momentum-1h": "..."}
    last_bar_time: Dict[str, str] = field(default_factory=dict)
    # Per-strategy positions: {"btc-trend-4h": {...}, "btc-momentum-1h": {...}}
    positions: Dict[str, Any] = field(default_factory=dict)
    # Per-strategy pending limit orders: {"btc-trend-4h": {"order_id": "...", "price": 123.4, "side": "buy", "quantity": 0.01, "bar_placed": 5, "type": "entry"}}
    pending_orders: Dict[str, Any] = field(default_factory=dict)
    last_signal: str = "none"
    error_count: int = 0
    last_error: str = ""

class StateManager:
    """SQLite based state persistence."""

    def __init__(self, state_dir: str = "bot/state"):
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.state_dir / "bot_state.db"
        self._conn: Optional[sqlite3.Connection] = None

    def connect(self) -> None:
        """Open database connection and create tables."""
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._create_tables()
        logger.info(f"State manager connected: {self.db_path}")

    def close(self) -> None:
        """Close database connection."""
        if self._conn:
            self._conn.close()

    def _create_tables(self) -> None:
        """Create tables if they don't exist."""
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS bot_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                is_running INTEGER DEFAULT 0,
                last_bar_time TEXT DEFAULT '{}',
                positions TEXT,
                pending_orders TEXT,
                current_position TEXT,
                last_signal TEXT DEFAULT 'none',
                error_count INTEGER DEFAULT 0,
                last_error TEXT DEFAULT '',
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_time TEXT NOT NULL,
                exit_time TEXT,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL,
                quantity REAL NOT NULL,
                pnl REAL,
                reason TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                event_type TEXT NOT NULL,
                data TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self._conn.commit()

        # Initialize bot_state row if empty
        row = self._conn.execute("SELECT COUNT(*) FROM bot_state").fetchone()
        if row[0] == 0:
            self._conn.execute("INSERT INTO bot_state (id) VALUES (1)")
            self._conn.commit()

    def load(self) -> BotState:
        """Load current bot state."""
        row = self._conn.execute("SELECT * FROM bot_state WHERE id = 1").fetchone()
        if row is None:
            return BotState()

        # Parse last_bar_time — handle both old string and new dict format
        lbt_raw = row["last_bar_time"] or "{}"
        try:
            last_bar_time = json.loads(lbt_raw)
            if isinstance(last_bar_time, str):
                # Legacy: single string, migrate to dict
                last_bar_time = {"btc-trend-4h": last_bar_time}
        except (json.JSONDecodeError, TypeError):
            # Not JSON — likely legacy ISO string
            if lbt_raw and lbt_raw != "{}":
                last_bar_time = {"btc-trend-4h": lbt_raw}
            else:
                last_bar_time = {}

        # Parse positions — support both new per-strategy and legacy single position
        positions_raw = row["positions"]
        if positions_raw:
            positions = json.loads(positions_raw)
        else:
            positions = {}
            # Migrate legacy current_position
            legacy_pos = json.loads(row["current_position"]) if row["current_position"] else None
            if legacy_pos:
                positions["btc-trend-4h"] = legacy_pos

        return BotState(
            is_running=bool(row["is_running"]),
            last_bar_time=last_bar_time,
            positions=positions,
            pending_orders=json.loads(row["pending_orders"]) if row["pending_orders"] else {},
            last_signal=row["last_signal"],
            error_count=row["error_count"],
            last_error=row["last_error"],
        )

    def save(self, state: BotState) -> None:
        """Save bot state."""
        # Handle last_bar_time: support both legacy string and new dict format
        if isinstance(state.last_bar_time, str):
            lbt_json = json.dumps({"btc-trend-4h": state.last_bar_time})
        else:
            lbt_json = json.dumps(state.last_bar_time)

        positions_json = json.dumps(state.positions) if state.positions else None
        pending_orders_json = json.dumps(state.pending_orders) if state.pending_orders else None

        # Keep writing legacy current_position for migration compatibility
        if state.positions and "btc-trend-4h" in state.positions:
            cp_json = json.dumps(state.positions["btc-trend-4h"])
        else:
            cp_json = None

        self._conn.execute("""
            UPDATE bot_state SET
                is_running = ?,
                last_bar_time = ?,
                positions = ?,
                pending_orders = ?,
                current_position = ?,
                last_signal = ?,
                error_count = ?,
                last_error = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = 1
        """, (
            state.is_running,
            lbt_json,
            positions_json,
            pending_orders_json,
            cp_json,
            state.last_signal,
            state.error_count,
            state.last_error,
        ))
        self._conn.commit()

    def record_trade(
        self,
        entry_time: str,
        symbol: str,
        side: str,
        entry_price: float,
        quantity: float,
        exit_time: str = "",
        exit_price: float = 0.0,
        pnl: float = 0.0,
        reason: str = "",
    ) -> None:
        """Record a completed trade."""
        self._conn.execute("""
            INSERT INTO trades (entry_time, exit_time, symbol, side, entry_price, exit_price, quantity, pnl, reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (entry_time, exit_time, symbol, side, entry_price, exit_price, quantity, pnl, reason))
        self._conn.commit()
        logger.info(f"Trade recorded: {side} {symbol} @ {entry_price} pnl={pnl:.2f}")

    def record_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """Record a bot event (signal, error, etc.)."""
        self._conn.execute("""
            INSERT INTO events (timestamp, event_type, data)
            VALUES (?, ?, ?)
        """, (datetime.now(UTC).isoformat(), event_type, json.dumps(data)))
        self._conn.commit()

    def fetch_trades(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Fetch recent trades."""
        rows = self._conn.execute(
            "SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(row) for row in rows]

    def fetch_events(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Fetch recent events."""
        rows = self._conn.execute(
            "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(row) for row in rows]
