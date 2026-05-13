#!/usr/bin/env python3
"""
v40 vs MACD+EMA Strategy Comparison on Hyperliquid BTC Perp

Compares the existing v40 trend-following strategy against MACD+EMA variants
across multiple timeframes using the SAME strict execution assumptions:
  - Next-bar-open execution
  - Hyperliquid taker fees (4.5 bps per side)
  - Conservative stop execution
  - Real hourly funding history
  - Baseline realistic slippage (3 bps entry/exit, 8 bps stop)
  - Reliable continuous window only (2024-01-31 onwards)
  - 2% risk per trade, ATR(14)*3 stop for ALL variants

Strategy A (v40):
  EMA50/EMA200 crossover on 4h + daily EMA200 regime filter

Strategy B variants:
  MACD(12,26,9) crossover + EMA50 regime filter + MACD inverse exit + ATR stop
  Tested on: 4h, 2h, 1h
  Bonus: MACD on 4h with daily EMA200 regime (matching v40's cross-timeframe filter)
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple, Optional

import numpy as np
import pandas as pd
import requests

HYPERLIQUID_INFO = "https://api.hyperliquid.xyz/info"
REPO_ROOT = Path(__file__).resolve().parents[1]


# ──────────────────────────────────────────────────────────
# Data Fetching (reused from v40 strict validation)
# ──────────────────────────────────────────────────────────

def dt_to_ms(s: str) -> int:
    return int(datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)


def now_floor_4h_ms() -> int:
    now = pd.Timestamp.now(tz="UTC")
    floored_hour = (now.hour // 4) * 4
    floored = now.floor("D") + pd.Timedelta(hours=floored_hour)
    return int(floored.timestamp() * 1000)


def fetch_hyperliquid_klines(
    coin: str, interval: str, start_ms: int, end_ms: int, chunk_days: int
) -> pd.DataFrame:
    out: List[dict] = []
    cur = start_ms
    chunk_ms = chunk_days * 24 * 60 * 60 * 1000
    while cur < end_ms:
        chunk_end = min(cur + chunk_ms, end_ms)
        payload = {
            "type": "candleSnapshot",
            "req": {"coin": coin, "interval": interval, "startTime": cur, "endTime": chunk_end},
        }
        r = requests.post(HYPERLIQUID_INFO, json=payload, timeout=60)
        r.raise_for_status()
        data = r.json()
        out.extend(data)
        cur = chunk_end
        time.sleep(0.06)
    if not out:
        raise RuntimeError(f"No Hyperliquid data for {coin} {interval}")
    df = pd.DataFrame(out)
    df["t"] = pd.to_datetime(df["t"], unit="ms", utc=True)
    for c in ["o", "h", "l", "c", "v"]:
        df[c] = df[c].astype(float)
    df = df.rename(columns={"t": "time", "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
    df = df.set_index("time")[["open", "high", "low", "close", "volume"]].sort_index()
    df = df[~df.index.duplicated(keep="last")]
    return df


def fetch_funding_history(coin: str, start_ms: int, end_ms: int, chunk_hours: int = 400) -> pd.DataFrame:
    out: List[dict] = []
    cur = start_ms
    chunk_ms = chunk_hours * 60 * 60 * 1000
    while cur < end_ms:
        chunk_end = min(cur + chunk_ms, end_ms)
        payload = {"type": "fundingHistory", "coin": coin, "startTime": cur, "endTime": chunk_end}
        r = requests.post(HYPERLIQUID_INFO, json=payload, timeout=60)
        r.raise_for_status()
        data = r.json()
        out.extend(data)
        cur = chunk_end + 1
        time.sleep(0.06)
    if not out:
        raise RuntimeError(f"No funding history for {coin}")
    df = pd.DataFrame(out)
    df["time"] = pd.to_datetime(df["time"], unit="ms", utc=True)
    df["fundingRate"] = df["fundingRate"].astype(float)
    cols = ["fundingRate"]
    df = df.set_index("time")[cols].sort_index()
    df = df[~df.index.duplicated(keep="last")]
    return df


def longest_continuous_segment(df: pd.DataFrame, freq: str) -> Tuple[pd.Timestamp, pd.Timestamp, int]:
    if df.empty:
        raise RuntimeError("Cannot compute longest continuous segment on empty dataframe")
    step = pd.Timedelta(freq)
    segments: List[Tuple[pd.Timestamp, pd.Timestamp, int]] = []
    start = df.index[0]
    prev = df.index[0]
    count = 1
    for ts in df.index[1:]:
        if ts - prev == step:
            count += 1
        else:
            segments.append((start, prev, count))
            start = ts
            count = 1
        prev = ts
    segments.append((start, prev, count))
    return max(segments, key=lambda x: x[2])


def missing_bar_stats(df: pd.DataFrame, freq: str) -> Dict[str, object]:
    expected = pd.date_range(df.index.min(), df.index.max(), freq=freq, tz="UTC")
    missing = expected.difference(df.index)
    return {
        "bars": int(len(df)),
        "duplicates": int(df.index.duplicated().sum()),
        "missing_bars": int(len(missing)),
        "first": df.index.min().isoformat(),
        "last": df.index.max().isoformat(),
    }


# ──────────────────────────────────────────────────────────
# Technical Indicators
# ──────────────────────────────────────────────────────────

def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [(df["high"] - df["low"]), (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False).mean()


def macd_components(df: pd.DataFrame) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Return (macd_line, signal_line, histogram)."""
    ema12 = df["close"].ewm(span=12, adjust=False).mean()
    ema26 = df["close"].ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


# ──────────────────────────────────────────────────────────
# Feature Builders
# ──────────────────────────────────────────────────────────

def build_features_v40(df_4h: pd.DataFrame, df_daily: pd.DataFrame) -> pd.DataFrame:
    """V40: EMA50/EMA200 crossover on 4h + daily EMA200 regime filter."""
    out = df_4h.copy()
    out["ema50"] = out["close"].ewm(span=50, adjust=False).mean()
    out["ema200"] = out["close"].ewm(span=200, adjust=False).mean()
    out["atr"] = atr(out, 14)

    daily = df_daily.copy()
    daily = daily.loc[daily.index >= pd.Timestamp("2021-01-01", tz="UTC")].copy()
    daily["ema200_daily"] = daily["close"].ewm(span=200, adjust=False).mean()
    daily["ema200_daily_completed"] = daily["ema200_daily"].shift(1)
    regime = daily[["ema200_daily_completed"]].reindex(out.index, method="ffill")
    out["ema200_daily"] = regime["ema200_daily_completed"]

    out["regime_long"] = (out["close"] > out["ema200_daily"]).astype(int)
    out["regime_short"] = (out["close"] < out["ema200_daily"]).astype(int)
    out["long_signal"] = (
        (out["close"] > out["ema50"]) & (out["ema50"] > out["ema200"]) & (out["regime_long"] == 1)
    )
    out["short_signal"] = (
        (out["close"] < out["ema50"]) & (out["ema50"] < out["ema200"]) & (out["regime_short"] == 1)
    )
    out["long_exit_signal"] = out["ema50"] < out["ema200"]
    out["short_exit_signal"] = out["ema50"] > out["ema200"]
    return out


def build_features_macd(
    df: pd.DataFrame,
    regime_type: str = "ema50",
    df_daily: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """
    MACD(12,26,9) crossover signals with regime filter.

    regime_type:
      - 'ema50': price above/below EMA(50) on the same timeframe
      - 'daily_ema200': daily EMA200 regime (cross-timeframe, matches v40)
    """
    out = df.copy()
    macd_line, signal_line, _ = macd_components(out)
    out["atr"] = atr(out, 14)

    # Detect crosses using previous bar comparison
    macd_above = macd_line > signal_line
    macd_above_prev = macd_line.shift(1) > signal_line.shift(1)
    out["macd_bullish"] = macd_above & ~macd_above_prev
    out["macd_bearish"] = ~macd_above & macd_above_prev

    if regime_type == "ema50":
        ema = out["close"].ewm(span=50, adjust=False).mean()
        out["above_regime"] = out["close"] > ema
        out["below_regime"] = out["close"] < ema
    elif regime_type == "daily_ema200":
        if df_daily is None:
            raise ValueError("df_daily required for daily_ema200 regime")
        daily = df_daily.copy()
        daily = daily.loc[daily.index >= pd.Timestamp("2021-01-01", tz="UTC")].copy()
        daily["ema200_daily"] = daily["close"].ewm(span=200, adjust=False).mean()
        daily["ema200_daily_completed"] = daily["ema200_daily"].shift(1)
        regime = daily[["ema200_daily_completed"]].reindex(out.index, method="ffill")
        out["regime_ema"] = regime["ema200_daily_completed"]
        out["above_regime"] = out["close"] > out["regime_ema"]
        out["below_regime"] = out["close"] < out["regime_ema"]

    # Entry: MACD bullish/bearish cross + regime alignment
    out["long_signal"] = out["macd_bullish"] & out["above_regime"]
    out["short_signal"] = out["macd_bearish"] & out["below_regime"]

    # Exit: MACD inverse cross (stop is handled by engine)
    out["long_exit_signal"] = out["macd_bearish"]
    out["short_exit_signal"] = out["macd_bullish"]

    return out


# ──────────────────────────────────────────────────────────
# Strict Execution Engine (identical to v40 validation)
# ──────────────────────────────────────────────────────────

@dataclass
class ScenarioConfig:
    name: str
    fee_rate: float
    entry_slippage_bps: float
    exit_slippage_bps: float
    stop_slippage_bps: float
    funding_mode: str  # "actual_signed" | "adverse_only"
    funding_multiplier: float
    description: str
    initial_capital: float = 10_000.0
    risk_pct: float = 0.02
    stop_atr_mult: float = 3.0
    warmup_bars: int = 220


def apply_slippage(price: float, side: str, action: str, bps: float) -> float:
    bump = bps / 10_000.0
    if action == "entry":
        return price * (1 + bump) if side == "long" else price * (1 - bump)
    return price * (1 - bump) if side == "long" else price * (1 + bump)


def funding_pnl_for_window(
    funding: pd.DataFrame,
    hourly_prices: pd.Series,
    entry_time: pd.Timestamp,
    exit_time: pd.Timestamp,
    side: str,
    qty: float,
    mode: str,
    multiplier: float,
) -> float:
    window = funding.loc[(funding.index > entry_time) & (funding.index <= exit_time)].copy()
    if window.empty:
        return 0.0
    prices = hourly_prices.reindex(window.index, method="ffill")
    sign = 1.0 if side == "long" else -1.0
    signed = -sign * window["fundingRate"] * qty * prices
    if mode == "actual_signed":
        pnl = signed
    elif mode == "adverse_only":
        pnl = signed.clip(upper=0.0) * multiplier
        return float(pnl.sum())
    else:
        raise ValueError(f"Unknown funding mode: {mode}")
    return float((pnl * multiplier).sum())


class StrictEngine:
    """Next-bar-open execution engine with fees, funding, and conservative stops."""

    def __init__(self, scenario: ScenarioConfig, funding: pd.DataFrame, hourly_prices: pd.Series):
        self.scenario = scenario
        self.funding = funding
        self.hourly_prices = hourly_prices

    def trade_fee(self, qty: float, price: float) -> float:
        return qty * price * self.scenario.fee_rate

    def run(self, df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame, Dict[str, float]]:
        position = 0  # 1=long, -1=short, 0=flat
        qty = 0.0
        entry = 0.0
        entry_time: pd.Timestamp | None = None
        signal_time: pd.Timestamp | None = None
        stop = np.nan
        realized = 0.0
        trades: List[dict] = []
        equity_rows: List[dict] = []
        funding_total = 0.0
        fee_total = 0.0

        for i in range(self.scenario.warmup_bars, len(df) - 1):
            t = df.index[i]
            b = df.iloc[i]
            next_bar = df.iloc[i + 1]
            next_time = df.index[i + 1]

            # Skip bars with missing indicators
            if np.isnan(b["atr"]) or np.isnan(b.get("ema50", 1.0)) or np.isnan(b.get("ema200", b.get("close", 1.0))):
                continue

            exited_this_bar = False

            # --- Stop check: intrabar, conservative fill ---
            if position == 1:
                stop_hit = b["low"] <= stop
                if stop_hit:
                    raw_exit = min(float(stop), float(b["open"]))
                    exit_px = apply_slippage(raw_exit, "long", "exit", self.scenario.stop_slippage_bps)
                    funding_pnl = funding_pnl_for_window(
                        self.funding, self.hourly_prices,
                        entry_time, t, "long", qty,
                        self.scenario.funding_mode, self.scenario.funding_multiplier,
                    )
                    fees = self.trade_fee(qty, entry) + self.trade_fee(qty, exit_px)
                    pnl = qty * (exit_px - entry) - fees + funding_pnl
                    realized += pnl
                    funding_total += funding_pnl
                    fee_total += fees
                    trades.append(self._make_trade(
                        signal_time, entry_time, t, "long", entry, exit_px, qty, pnl, fees, funding_pnl, "stop",
                    ))
                    position, qty, entry, entry_time, signal_time, stop = 0, 0.0, 0.0, None, None, np.nan
                    exited_this_bar = True

            elif position == -1:
                stop_hit = b["high"] >= stop
                if stop_hit:
                    raw_exit = max(float(stop), float(b["open"]))
                    exit_px = apply_slippage(raw_exit, "short", "exit", self.scenario.stop_slippage_bps)
                    funding_pnl = funding_pnl_for_window(
                        self.funding, self.hourly_prices,
                        entry_time, t, "short", qty,
                        self.scenario.funding_mode, self.scenario.funding_multiplier,
                    )
                    fees = self.trade_fee(qty, entry) + self.trade_fee(qty, exit_px)
                    pnl = qty * (entry - exit_px) - fees + funding_pnl
                    realized += pnl
                    funding_total += funding_pnl
                    fee_total += fees
                    trades.append(self._make_trade(
                        signal_time, entry_time, t, "short", entry, exit_px, qty, pnl, fees, funding_pnl, "stop",
                    ))
                    position, qty, entry, entry_time, signal_time, stop = 0, 0.0, 0.0, None, None, np.nan
                    exited_this_bar = True

            # --- Exit signal: execute on NEXT bar open ---
            if not exited_this_bar and position == 1 and bool(b["long_exit_signal"]):
                exit_px = apply_slippage(float(next_bar["open"]), "long", "exit", self.scenario.exit_slippage_bps)
                funding_pnl = funding_pnl_for_window(
                    self.funding, self.hourly_prices,
                    entry_time, next_time, "long", qty,
                    self.scenario.funding_mode, self.scenario.funding_multiplier,
                )
                fees = self.trade_fee(qty, entry) + self.trade_fee(qty, exit_px)
                pnl = qty * (exit_px - entry) - fees + funding_pnl
                realized += pnl
                funding_total += funding_pnl
                fee_total += fees
                trades.append(self._make_trade(
                    signal_time, entry_time, next_time, "long", entry, exit_px, qty, pnl, fees, funding_pnl,
                    "signal_exit",
                ))
                position, qty, entry, entry_time, signal_time, stop = 0, 0.0, 0.0, None, None, np.nan

            elif not exited_this_bar and position == -1 and bool(b["short_exit_signal"]):
                exit_px = apply_slippage(float(next_bar["open"]), "short", "exit", self.scenario.exit_slippage_bps)
                funding_pnl = funding_pnl_for_window(
                    self.funding, self.hourly_prices,
                    entry_time, next_time, "short", qty,
                    self.scenario.funding_mode, self.scenario.funding_multiplier,
                )
                fees = self.trade_fee(qty, entry) + self.trade_fee(qty, exit_px)
                pnl = qty * (entry - exit_px) - fees + funding_pnl
                realized += pnl
                funding_total += funding_pnl
                fee_total += fees
                trades.append(self._make_trade(
                    signal_time, entry_time, next_time, "short", entry, exit_px, qty, pnl, fees, funding_pnl,
                    "signal_exit",
                ))
                position, qty, entry, entry_time, signal_time, stop = 0, 0.0, 0.0, None, None, np.nan

            # --- Entry: execute on NEXT bar open ---
            if position == 0:
                equity_now = self.scenario.initial_capital + realized
                risk_usd = equity_now * self.scenario.risk_pct
                atr_dist = self.scenario.stop_atr_mult * float(b["atr"])

                if bool(b["long_signal"]) and atr_dist > 0:
                    raw_entry = float(next_bar["open"])
                    entry_px = apply_slippage(raw_entry, "long", "entry", self.scenario.entry_slippage_bps)
                    stop_px = entry_px - atr_dist
                    q = risk_usd / atr_dist
                    if q > 0:
                        position, qty, entry, entry_time, signal_time, stop = (
                            1, float(q), entry_px, next_time, t, float(stop_px),
                        )

                elif bool(b["short_signal"]) and atr_dist > 0:
                    raw_entry = float(next_bar["open"])
                    entry_px = apply_slippage(raw_entry, "short", "entry", self.scenario.entry_slippage_bps)
                    stop_px = entry_px + atr_dist
                    q = risk_usd / atr_dist
                    if q > 0:
                        position, qty, entry, entry_time, signal_time, stop = (
                            -1, float(q), entry_px, next_time, t, float(stop_px),
                        )

            # Equity curve (mark-to-market at bar close)
            open_pnl = 0.0
            if position == 1:
                open_pnl = qty * (b["close"] - entry)
            elif position == -1:
                open_pnl = qty * (entry - b["close"])
            equity_rows.append({"time": t, "equity": self.scenario.initial_capital + realized + open_pnl})

        # Force-close at end of data
        if position != 0 and entry_time is not None:
            t = df.index[-1]
            px = float(df.iloc[-1]["close"])
            side = "long" if position == 1 else "short"
            exit_px = apply_slippage(px, side, "exit", self.scenario.exit_slippage_bps)
            gross_pnl = qty * (exit_px - entry) if position == 1 else qty * (entry - exit_px)
            funding_pnl = funding_pnl_for_window(
                self.funding, self.hourly_prices,
                entry_time, t, side, qty,
                self.scenario.funding_mode, self.scenario.funding_multiplier,
            )
            fees = self.trade_fee(qty, entry) + self.trade_fee(qty, exit_px)
            pnl = gross_pnl - fees + funding_pnl
            funding_total += funding_pnl
            fee_total += fees
            trades.append(self._make_trade(
                signal_time, entry_time, t, side, entry, exit_px, qty, pnl, fees, funding_pnl, "eod",
            ))

        trades_df = pd.DataFrame(trades) if trades else pd.DataFrame()
        eq_df = pd.DataFrame(equity_rows).set_index("time") if equity_rows else pd.DataFrame(columns=["equity"])
        cost_breakdown = {"fees_total": round(float(fee_total), 2), "funding_total": round(float(funding_total), 2)}
        return trades_df, eq_df, cost_breakdown

    def _make_trade(self, signal_time, entry_time, exit_time, dir_, entry, exit_px, qty, pnl, fees, funding, reason):
        return {
            "signal_time": signal_time,
            "entry_time": entry_time,
            "exit_time": exit_time,
            "dir": dir_,
            "entry": entry,
            "exit": exit_px,
            "qty": qty,
            "gross_pnl": qty * (exit_px - entry) if dir_ == "long" else qty * (entry - exit_px),
            "fees": -fees,
            "funding": funding,
            "pnl": pnl,
            "hold_hours": (exit_time - entry_time).total_seconds() / 3600,
            "reason": reason,
        }


# ──────────────────────────────────────────────────────────
# Metrics
# ──────────────────────────────────────────────────────────

def sortino_from_equity(eq: pd.Series) -> float:
    rets = eq.pct_change().dropna()
    if rets.empty:
        return 0.0
    downside = rets[rets < 0]
    if len(downside) < 2 or downside.std() == 0:
        return 0.0
    return float(rets.mean() / downside.std() * np.sqrt(2190))


def compute_metrics(trades: pd.DataFrame, eq: pd.DataFrame, initial_capital: float) -> Dict[str, float]:
    if eq.empty:
        return {k: 0.0 for k in ["return_pct", "cagr", "max_dd", "pf", "wr", "trades", "sharpe", "sortino", "avg_hold_hours"]}

    eq_s = eq["equity"].dropna()
    if len(eq_s) < 2:
        return {k: 0.0 for k in ["return_pct", "cagr", "max_dd", "pf", "wr", "trades", "sharpe", "sortino", "avg_hold_hours"]}

    ret = (eq_s.iloc[-1] / initial_capital - 1) * 100
    years = (eq_s.index[-1] - eq_s.index[0]).total_seconds() / (365.25 * 86400)
    cagr = ((eq_s.iloc[-1] / initial_capital) ** (1 / years) - 1) * 100 if years > 0 else 0.0
    peak = eq_s.cummax()
    dd = (eq_s / peak - 1) * 100
    max_dd = float(dd.min())

    rets = eq_s.pct_change().dropna()
    sharpe = float(rets.mean() / rets.std() * np.sqrt(2190)) if len(rets) > 1 and rets.std() > 0 else 0.0
    sortino = sortino_from_equity(eq_s)

    if trades.empty:
        pf, wr, avg_hold = 0.0, 0.0, 0.0
    else:
        pnls = trades["pnl"]
        wins = pnls[pnls > 0]
        losses = pnls[pnls <= 0]
        wr = float((pnls > 0).mean() * 100)
        pf = float(wins.sum() / abs(losses.sum())) if len(losses) > 0 and abs(losses.sum()) > 0 else math.inf
        avg_hold = float(trades["hold_hours"].mean())

    return {
        "return_pct": round(float(ret), 2),
        "cagr": round(float(cagr), 2),
        "max_dd": round(max_dd, 2),
        "pf": round(float(pf), 2) if np.isfinite(pf) else 0.0,
        "wr": round(float(wr), 2),
        "trades": int(len(trades)),
        "sharpe": round(float(sharpe), 3),
        "sortino": round(float(sortino), 3),
        "avg_hold_hours": round(float(avg_hold), 1),
    }


def buy_hold_with_funding(
    df: pd.DataFrame,
    funding: pd.DataFrame,
    hourly_prices: pd.Series,
    fee_rate: float,
    initial_capital: float,
) -> Dict[str, float]:
    start_t = df.index[0]
    end_t = df.index[-1]
    entry_px = float(df.iloc[0]["open"]) * (1 + fee_rate)
    qty = initial_capital / entry_px
    exit_px = float(df.iloc[-1]["close"]) * (1 - fee_rate)
    fees = qty * entry_px * fee_rate + qty * exit_px * fee_rate
    funding_pnl = funding_pnl_for_window(funding, hourly_prices, start_t, end_t, "long", qty, "actual_signed", 1.0)
    final_equity = initial_capital + qty * (exit_px - entry_px) - fees + funding_pnl
    ret = (final_equity / initial_capital - 1) * 100
    years = (end_t - start_t).total_seconds() / (365.25 * 86400)
    cagr = ((final_equity / initial_capital) ** (1 / years) - 1) * 100 if years > 0 else 0.0
    eq = (df["close"] / entry_px) * initial_capital
    dd = (eq / eq.cummax() - 1) * 100
    return {
        "return_pct": round(float(ret), 2),
        "cagr": round(float(cagr), 2),
        "max_dd": round(float(dd.min()), 2),
        "funding_pnl": round(float(funding_pnl), 2),
    }


def compute_sharpe(eq_s: pd.Series) -> float:
    rets = eq_s.pct_change().dropna()
    if len(rets) < 2 or rets.std() == 0:
        return 0.0
    return float(rets.mean() / rets.std() * np.sqrt(2190))


def compute_sortino(eq_s: pd.Series) -> float:
    rets = eq_s.pct_change().dropna()
    if rets.empty:
        return 0.0
    downside = rets[rets < 0]
    if len(downside) < 2 or downside.std() == 0:
        return 0.0
    return float(rets.mean() / downside.std() * np.sqrt(2190))


# ──────────────────────────────────────────────────────────
# Resample 1h data to 2h (preserving OHLCV integrity)
# ──────────────────────────────────────────────────────────

def resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Proper OHLCV resampling."""
    return df.resample(rule).agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna()


# ──────────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────────

def main() -> None:
    out_dir = REPO_ROOT / "docs/backtests/v40_vs_macd_comparison"
    out_dir.mkdir(parents=True, exist_ok=True)

    end_ms = now_floor_4h_ms() - 1

    print("=" * 80)
    print("FETCHING HYPERLIQUID BTC DATA")
    print("=" * 80)

    # Fetch all timeframes
    print("\n[1/5] Fetching 4h klines...")
    df_4h_full = fetch_hyperliquid_klines("BTC", "4h", dt_to_ms("2022-01-01"), end_ms, chunk_days=120)

    print("[2/5] Fetching 1h klines (for 2h resample + 1h strategy)...")
    df_1h_full = fetch_hyperliquid_klines("BTC", "1h", dt_to_ms("2022-01-01"), end_ms, chunk_days=45)

    print("[3/5] Fetching daily klines...")
    df_daily_full = fetch_hyperliquid_klines("BTC", "1d", dt_to_ms("2021-01-01"), end_ms, chunk_days=365)

    # Find longest continuous 4h segment — this is our reliable window
    reliable_4h_start, reliable_4h_end, reliable_bars = longest_continuous_segment(df_4h_full, "4h")
    print(f"\nReliable 4h window: {reliable_4h_start.isoformat()} → {reliable_4h_end.isoformat()} ({reliable_bars} bars)")

    # Window the data
    strict_4h = df_4h_full.loc[(df_4h_full.index >= reliable_4h_start) & (df_4h_full.index <= reliable_4h_end)].copy()
    strict_1h = df_1h_full.loc[(df_1h_full.index >= reliable_4h_start) & (df_1h_full.index <= reliable_4h_end)].copy()
    strict_daily = df_daily_full.loc[df_daily_full.index <= reliable_4h_end].copy()

    # Resample 1h → 2h
    strict_2h = resample_ohlcv(strict_1h, "2h")

    print(f"4h bars: {len(strict_4h)}, 2h bars: {len(strict_2h)}, 1h bars: {len(strict_1h)}, daily bars: {len(strict_daily)}")

    # Funding history
    print("[4/5] Fetching funding history...")
    funding_start = int(reliable_4h_start.timestamp() * 1000)
    funding_end = int(reliable_4h_end.timestamp() * 1000) + 1
    funding = fetch_funding_history("BTC", funding_start, funding_end)

    # Hourly prices for funding notional calculations
    hourly_prices = strict_1h["close"].reindex(funding.index, method="ffill")

    print("[5/5] Data fetch complete.\n")

    # ──── Build all feature DataFrames ────
    print("=" * 80)
    print("BUILDING FEATURES")
    print("=" * 80)

    feat_v40_4h = build_features_v40(strict_4h, strict_daily)
    feat_macd_4h_ema50 = build_features_macd(strict_4h, regime_type="ema50")
    feat_macd_4h_daily = build_features_macd(strict_4h, regime_type="daily_ema200", df_daily=strict_daily)
    feat_macd_2h_ema50 = build_features_macd(strict_2h, regime_type="ema50")
    feat_macd_1h_ema50 = build_features_macd(strict_1h, regime_type="ema50")

    print(f"Feature shapes: v40_4h={feat_v40_4h.shape}, macd_4h={feat_macd_4h_ema50.shape}, "
          f"macd_2h={feat_macd_2h_ema50.shape}, macd_1h={feat_macd_1h_ema50.shape}")

    # ──── Define variants ────
    variants = [
        {
            "id": "v40_4h",
            "label": "v40 (EMA50/200 + daily EMA200) 4h",
            "features": feat_v40_4h,
            "timeframe": "4h",
            "description": "EMA50/EMA200 crossover + daily EMA200 regime, ATR(14)*3 stop",
        },
        {
            "id": "macd_ema50_4h",
            "label": "MACD + EMA50 4h",
            "features": feat_macd_4h_ema50,
            "timeframe": "4h",
            "description": "MACD(12,26,9) cross + EMA50 regime, ATR(14)*3 stop, MACD inverse exit",
        },
        {
            "id": "macd_daily_4h",
            "label": "MACD + daily EMA200 4h",
            "features": feat_macd_4h_daily,
            "timeframe": "4h",
            "description": "MACD(12,26,9) cross + daily EMA200 regime (matching v40), ATR(14)*3 stop",
        },
        {
            "id": "macd_ema50_2h",
            "label": "MACD + EMA50 2h",
            "features": feat_macd_2h_ema50,
            "timeframe": "2h",
            "description": "MACD(12,26,9) cross + EMA50 regime, ATR(14)*3 stop, 2h bars",
        },
        {
            "id": "macd_ema50_1h",
            "label": "MACD + EMA50 1h",
            "features": feat_macd_1h_ema50,
            "timeframe": "1h",
            "description": "MACD(12,26,9) cross + EMA50 regime, ATR(14)*3 stop, 1h bars",
        },
    ]

    # ──── Baseline realistic scenario (used for ALL variants) ────
    baseline_scenario = ScenarioConfig(
        name="baseline_realistic",
        fee_rate=0.00045,
        entry_slippage_bps=3.0,
        exit_slippage_bps=3.0,
        stop_slippage_bps=8.0,
        funding_mode="actual_signed",
        funding_multiplier=1.0,
        description="Next-open, taker fees, moderate slippage, exact signed funding, 2% risk, ATR*3 stop",
    )

    # ──── Run all variants ────
    print("\n" + "=" * 80)
    print("RUNNING BACKTESTS")
    print("=" * 80)

    results: List[dict] = []
    all_details: Dict[str, dict] = {}

    for variant in variants:
        print(f"\n  Running: {variant['label']} ...")
        engine = StrictEngine(baseline_scenario, funding, hourly_prices)
        trades, eq, costs = engine.run(variant["features"])
        m = compute_metrics(trades, eq, baseline_scenario.initial_capital)

        # Extra diagnostics
        if not trades.empty:
            avg_win = trades[trades["pnl"] > 0]["pnl"].mean() if (trades["pnl"] > 0).any() else 0.0
            avg_loss = trades[trades["pnl"] <= 0]["pnl"].mean() if (trades["pnl"] <= 0).any() else 0.0
            long_trades = int((trades["dir"] == "long").sum())
            short_trades = int((trades["dir"] == "short").sum())
            stop_exits = int((trades["reason"] == "stop").sum())
            signal_exits = int((trades["reason"] == "signal_exit").sum())
        else:
            avg_win = avg_loss = long_trades = short_trades = stop_exits = signal_exits = 0.0

        result_row = {
            "variant": variant["id"],
            "strategy": variant["label"],
            "timeframe": variant["timeframe"],
            **m,
            **costs,
            "avg_win": round(float(avg_win), 2),
            "avg_loss": round(float(avg_loss), 2),
            "long_trades": long_trades,
            "short_trades": short_trades,
            "stop_exits": stop_exits,
            "signal_exits": signal_exits,
        }
        results.append(result_row)

        # Save per-variant CSVs
        prefix = out_dir / variant["id"]
        trades.to_csv(f"{prefix}_trades.csv", index=False)
        eq.to_csv(f"{prefix}_equity.csv")
        pd.DataFrame([result_row]).to_csv(f"{prefix}_summary.csv", index=False)

        all_details[variant["id"]] = {
            "config": asdict(baseline_scenario),
            "summary": m,
            "costs": costs,
            "extra": {
                "avg_win": avg_win, "avg_loss": avg_loss,
                "long_trades": long_trades, "short_trades": short_trades,
                "stop_exits": stop_exits, "signal_exits": signal_exits,
            },
        }

        print(f"    → Return: {m['return_pct']:+.2f}%  CAGR: {m['cagr']:+.2f}%  "
              f"MaxDD: {m['max_dd']:.1f}%  PF: {m['pf']:.2f}  "
              f"WR: {m['wr']:.1f}%  Trades: {m['trades']}  "
              f"Funding: ${costs['funding_total']:+.0f}  Fees: ${costs['fees_total']:.0f}")

    # ──── Baseline: passive long BTC buy & hold ────
    buy_hold = buy_hold_with_funding(strict_4h, funding, hourly_prices, 0.00045, 10_000.0)

    # ──── Comparison Table ────
    columns = [
        "variant", "timeframe", "return_pct", "cagr", "max_dd", "pf", "wr",
        "trades", "sharpe", "sortino", "avg_hold_hours",
        "fees_total", "funding_total",
        "avg_win", "avg_loss",
    ]
    comparison_df = pd.DataFrame(results)[columns]

    print("\n" + "=" * 80)
    print("COMPARISON TABLE")
    print("=" * 80)
    print(comparison_df.to_string(index=False))

    # ──── Verdict & Recommendation ────
    best = max(results, key=lambda r: r.get("sortino", -999))

    # Which strategy resists funding best?
    funding_per_trade = {r["variant"]: r["funding_total"] / r["trades"] if r["trades"] > 0 else -999 for r in results}

    print("\n" + "=" * 80)
    print("FUNDING ANALYSIS (avg per trade)")
    print("=" * 80)
    for vid, fpt in sorted(funding_per_trade.items(), key=lambda x: x[1], reverse=True):
        print(f"  {vid:25s}  ${fpt:+.1f}/trade")

    # ──── Generate verdict ────
    # Determine which variant handles funding best while maintaining positive return
    positive_variants = [r for r in results if r["return_pct"] > 0]
    negative_variants = [r for r in results if r["return_pct"] <= 0]

    v40_row = next(r for r in results if r["variant"] == "v40_4h")

    print("\n" + "=" * 80)
    print("VERDICT")
    print("=" * 80)

    verdict_lines = []

    # Compare v40 to MACD variants on same timeframe
    macd_variants_4h = [r for r in results if "macd" in r["variant"] and "4h" in r["variant"]]
    for mv in macd_variants_4h:
        if mv["return_pct"] > v40_row["return_pct"]:
            verdict_lines.append(f"✓ {mv['variant']} beats v40 on 4h: {mv['return_pct']:+.2f}% vs {v40_row['return_pct']:+.2f}%")
        else:
            verdict_lines.append(f"✗ {mv['variant']} underperforms v40 on 4h: {mv['return_pct']:+.2f}% vs {v40_row['return_pct']:+.2f}%")

    # Best overall
    verdict_lines.append(f"\nBest risk-adjusted return: {best['variant']} (Sortino={best.get('sortino', 'N/A')}, PF={best.get('pf', 'N/A')})")

    # Compare to buy & hold
    for r in results:
        if r["return_pct"] > buy_hold["return_pct"]:
            verdict_lines.append(f"★ {r['variant']} BEATS buy-&-hold ({buy_hold['return_pct']:+.2f}%)!")

    # Funding resistance
    min_funding_per_trade = min(funding_per_trade.values())
    best_funding = [k for k, v in funding_per_trade.items() if v == min_funding_per_trade][0]
    verdict_lines.append(f"\nBest funding resistance: {best_funding} (${min_funding_per_trade:+.1f}/trade)")

    # Recommendation
    verdict_lines.append("\n─── RECOMMENDATION ───")
    if positive_variants:
        # Sort by Sortino for risk-adjusted measure
        sorted_by_sortino = sorted(positive_variants, key=lambda r: r.get("sortino", -999), reverse=True)
        top = sorted_by_sortino[0]
        verdict_lines.append(f"Top variant for Hyperliquid testnet validation: {top['variant']}")
        verdict_lines.append(f"  Return: {top['return_pct']:+.2f}% | CAGR: {top['cagr']:+.2f}% | Sortino: {top['sortino']} | PF: {top['pf']}")
        verdict_lines.append(f"  Funding cost: ${top['funding_total']:+.0f} (${top['funding_total']/top['trades']:+.1f}/trade)" if top["trades"] > 0 else "")
    else:
        verdict_lines.append("NO variant produces positive returns on Hyperliquid with strict execution.")
        verdict_lines.append("Recommendation: REWORK strategy before testnet deployment.")

    verdict = "\n".join(verdict_lines)
    print(verdict)

    # ──── Save all outputs ────
    comparison_df.to_csv(out_dir / "comparison_table.csv", index=False)

    diagnostics = {
        "generated_at_utc": pd.Timestamp.now(tz="UTC").isoformat(),
        "methodology": {
            "execution": "next bar open for signal entries/exits; intrabar conservative stop fills",
            "fees": "Hyperliquid taker 0.045% per side",
            "slippage": "baseline_realistic: 3 bps entry/exit, 8 bps stop",
            "funding": "exact Hyperliquid hourly fundingHistory, notional via forward-filled 1h close",
            "reliable_window_only": True,
            "risk_model": "2% risk per trade, ATR(14)*3 stop for ALL variants",
        },
        "data_integrity": {
            "4h_window": missing_bar_stats(strict_4h, "4h"),
            "2h_window": missing_bar_stats(strict_2h, "2h"),
            "1h_window": missing_bar_stats(strict_1h, "1h"),
            "daily_window": missing_bar_stats(strict_daily, "1D"),
            "reliable_4h_start": reliable_4h_start.isoformat(),
            "reliable_4h_end": reliable_4h_end.isoformat(),
            "reliable_4h_bars": reliable_bars,
            "funding_bars": int(len(funding)),
        },
        "buy_hold": buy_hold,
        "results": results,
        "verdict": verdict,
    }
    (out_dir / "diagnostics.json").write_text(json.dumps(diagnostics, indent=2))
    (out_dir / "summary.json").write_text(json.dumps({
        "comparison_table": results,
        "buy_hold": buy_hold,
        "verdict": verdict,
    }, indent=2))

    # Markdown report
    lines = [
        "# v40 vs MACD+EMA Strategy Comparison — Hyperliquid BTC Perp",
        "",
        f"**Generated:** {pd.Timestamp.now(tz='UTC').isoformat()}",
        f"**Reliable window:** {reliable_4h_start.strftime('%Y-%m-%d')} → {reliable_4h_end.strftime('%Y-%m-%d')} ({reliable_bars} 4h bars)",
        "",
        "## Assumptions (IDENTICAL for ALL variants)",
        "- Next-bar-open execution for all signal-driven entries/exits",
        "- Hyperliquid taker fees: 4.5 bps per side",
        "- Slippage: 3 bps entry/exit, 8 bps stop (baseline realistic)",
        "- Exact hourly funding history from Hyperliquid API",
        "- Conservative stop execution: worse of stop vs bar open + stop slippage",
        "- 2% risk per trade, ATR(14)*3 stop for ALL variants",
        "- Daily EMA200 regime uses previous completed daily bar (no look-ahead)",
        "- Strategy logic unchanged from spec for each variant",
        "",
        "## Comparison Table",
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join(["---"] * len(columns)) + " |",
    ]
    for _, row in comparison_df.iterrows():
        lines.append("| " + " | ".join(str(row[c]) for c in columns) + " |")

    lines += [
        "",
        "## Baseline (Passive Long BTC Buy & Hold)",
        f"- Return: {buy_hold['return_pct']:+.2f}%, CAGR: {buy_hold['cagr']:+.2f}%, MaxDD: {buy_hold['max_dd']:.1f}%, Funding: ${buy_hold['funding_pnl']:+.0f}",
        "",
        "## Verdict",
        verdict.replace("\n", "\n\n"),
    ]
    (out_dir / "report.md").write_text("\n".join(lines))

    print(f"\nAll outputs saved to: {out_dir}")
    print("Files: comparison_table.csv, report.md, diagnostics.json, summary.json")
    print("Per-variant: <variant_id>_trades.csv, _equity.csv, _summary.csv")


if __name__ == "__main__":
    main()
