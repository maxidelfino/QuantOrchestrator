#!/usr/bin/env python3
"""v44 Hyperliquid BTC Strategy — Strict Validation

v44 = v40 base logic
    + ADX(14) > 20 entry filter
    + Funding rate filter: skip entry if 1h funding > 0.01% OR 8h avg funding > 0.005% (against position direction)
    - NO trailing stop
    - NO max hold time
    - NO profit target
    - Everything else identical to v40 (EMA50/200 crossover, daily EMA200 regime, ATR(14)*3 stop, 2% risk)
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
import requests

HYPERLIQUID_INFO = "https://api.hyperliquid.xyz/info"


# ── helpers ──────────────────────────────────────────────────────────────────

def dt_to_ms(s: str) -> int:
    return int(datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)


def now_floor_4h_ms() -> int:
    now = pd.Timestamp.now(tz="UTC")
    floored_hour = (now.hour // 4) * 4
    floored = now.floor("D") + pd.Timedelta(hours=floored_hour)
    return int(floored.timestamp() * 1000)


def fetch_hyperliquid_klines(coin: str, interval: str, start_ms: int, end_ms: int, chunk_days: int) -> pd.DataFrame:
    out: List[dict] = []
    cur = start_ms
    chunk_ms = chunk_days * 24 * 60 * 60 * 1000
    while cur < end_ms:
        chunk_end = min(cur + chunk_ms, end_ms)
        payload = {
            "type": "candleSnapshot",
            "req": {"coin": coin, "interval": interval, "startTime": cur, "endTime": chunk_end},
        }
        r = requests.post(HYPERLIQUID_INFO, json=payload, timeout=30)
        r.raise_for_status()
        data = r.json()
        out.extend(data)
        cur = chunk_end
        time.sleep(0.05)
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
        r = requests.post(HYPERLIQUID_INFO, json=payload, timeout=30)
        r.raise_for_status()
        data = r.json()
        out.extend(data)
        cur = chunk_end + 1
        time.sleep(0.05)
    if not out:
        raise RuntimeError(f"No funding history for {coin}")
    df = pd.DataFrame(out)
    df["time"] = pd.to_datetime(df["time"], unit="ms", utc=True)
    df["fundingRate"] = df["fundingRate"].astype(float)
    if "premium" in df.columns:
        df["premium"] = df["premium"].astype(float)
    cols = ["fundingRate"] + (["premium"] if "premium" in df.columns else [])
    df = df.set_index("time")[cols].sort_index()
    df = df[~df.index.duplicated(keep="last")]
    return df


def infer_earliest_hyperliquid_4h(coin: str = "BTC") -> pd.Timestamp:
    probes = [
        ("2021-01-01", "2021-12-31"),
        ("2022-01-01", "2022-12-31"),
        ("2023-01-01", "2023-12-31"),
    ]
    first = None
    for start, end in probes:
        payload = {
            "type": "candleSnapshot",
            "req": {"coin": coin, "interval": "4h", "startTime": dt_to_ms(start), "endTime": dt_to_ms(end)},
        }
        r = requests.post(HYPERLIQUID_INFO, json=payload, timeout=30)
        r.raise_for_status()
        data = r.json()
        if data:
            ts = pd.to_datetime(data[0]["t"], unit="ms", utc=True)
            first = ts if first is None else min(first, ts)
    if first is None:
        raise RuntimeError("Could not infer earliest Hyperliquid 4h BTC candle")
    return first


def missing_bar_stats(df: pd.DataFrame, freq: str) -> Dict[str, object]:
    expected = pd.date_range(df.index.min(), df.index.max(), freq=freq, tz="UTC")
    missing = expected.difference(df.index)
    return {
        "bars": int(len(df)),
        "duplicates": int(df.index.duplicated().sum()),
        "missing_bars": int(len(missing)),
        "first": df.index.min().isoformat(),
        "last": df.index.max().isoformat(),
        "sample_missing": [ts.isoformat() for ts in missing[:10]],
    }


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


# ── technical indicators ─────────────────────────────────────────────────────

def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [(df["high"] - df["low"]), (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False).mean()


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """ADX with Wilder's smoothing (ewm alpha=1/period)."""
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [(df["high"] - df["low"]), (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()], axis=1
    ).max(axis=1)
    atr_s = tr.ewm(alpha=1.0 / period, adjust=False).mean()

    up_move = df["high"] - df["high"].shift(1)
    down_move = df["low"].shift(1) - df["low"]
    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=df.index)

    plus_di = 100 * (plus_dm.ewm(alpha=1.0 / period, adjust=False).mean() / atr_s.replace(0, np.nan))
    minus_di = 100 * (minus_dm.ewm(alpha=1.0 / period, adjust=False).mean() / atr_s.replace(0, np.nan))

    dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1.0 / period, adjust=False).mean().fillna(0)


def compute_funding_features(df_4h: pd.DataFrame, funding: pd.DataFrame) -> pd.DataFrame:
    """Align hourly funding rates to 4h bars without look-ahead."""
    funding_1h_vals: List[float] = []
    funding_8h_vals: List[float] = []
    funding_s = funding["fundingRate"]

    for t in df_4h.index:
        mask = funding_s.index <= t
        relevant = funding_s[mask]
        if len(relevant) > 0:
            funding_1h_vals.append(float(relevant.iloc[-1]))
            n = min(8, len(relevant))
            funding_8h_vals.append(float(relevant.iloc[-n:].mean()))
        else:
            funding_1h_vals.append(0.0)
            funding_8h_vals.append(0.0)

    df_4h["funding_1h"] = funding_1h_vals
    df_4h["funding_8h_avg"] = funding_8h_vals
    return df_4h


def build_features_v44(df_4h: pd.DataFrame, df_daily: pd.DataFrame, funding: pd.DataFrame, warmup_daily_start: str = "2021-01-01") -> pd.DataFrame:
    out = df_4h.copy()

    # ── v40 core indicators ──
    out["ema50"] = out["close"].ewm(span=50, adjust=False).mean()
    out["ema200"] = out["close"].ewm(span=200, adjust=False).mean()
    out["atr"] = atr(out, 14)

    # ── v44 addition: ADX ──
    out["adx"] = adx(out, 14)

    # ── daily EMA200 regime (shifted 1 day, no look-ahead) ──
    daily = df_daily.copy()
    daily = daily.loc[daily.index >= pd.Timestamp(warmup_daily_start, tz="UTC")].copy()
    daily["ema200_daily"] = daily["close"].ewm(span=200, adjust=False).mean()
    daily["ema200_daily_completed"] = daily["ema200_daily"].shift(1)
    regime = daily[["ema200_daily_completed"]].reindex(out.index, method="ffill")
    out["ema200_daily"] = regime["ema200_daily_completed"]

    # ── regime & signals ──
    out["regime_long"] = (out["close"] > out["ema200_daily"]).astype(int)
    out["regime_short"] = (out["close"] < out["ema200_daily"]).astype(int)
    out["long_signal"] = (out["close"] > out["ema50"]) & (out["ema50"] > out["ema200"]) & (out["regime_long"] == 1)
    out["short_signal"] = (out["close"] < out["ema50"]) & (out["ema50"] < out["ema200"]) & (out["regime_short"] == 1)
    out["long_exit_signal"] = out["ema50"] < out["ema200"]
    out["short_exit_signal"] = out["ema50"] > out["ema200"]

    # ── v44 additions: ADX filter + funding filter ──
    out["adx_ok"] = out["adx"] > 20

    # funding features (aligned, no look-ahead)
    out = compute_funding_features(out, funding)

    # funding-rate filter flags (thresholds: 1h > 0.01%, 8h avg > 0.005%)
    FUNDING_THRESHOLD_1H = 0.0001   # 0.01%
    FUNDING_THRESHOLD_8H = 0.00005  # 0.005%

    # long: skip if funding_1h > 0.01% OR funding_8h_avg > 0.005% (you pay funding as long)
    out["funding_ok_long"] = ~((out["funding_1h"] > FUNDING_THRESHOLD_1H) | (out["funding_8h_avg"] > FUNDING_THRESHOLD_8H))
    # short: skip if funding_1h < -0.01% OR funding_8h_avg < -0.005% (you pay funding as short)
    out["funding_ok_short"] = ~((out["funding_1h"] < -FUNDING_THRESHOLD_1H) | (out["funding_8h_avg"] < -FUNDING_THRESHOLD_8H))

    return out


# ── scenario config ──────────────────────────────────────────────────────────

@dataclass
class ScenarioConfig:
    name: str
    fee_rate: float
    entry_slippage_bps: float
    exit_slippage_bps: float
    stop_slippage_bps: float
    funding_mode: str
    funding_multiplier: float
    description: str
    initial_capital: float = 10_000.0
    risk_pct: float = 0.02
    stop_atr_mult: float = 3.0
    warmup_bars: int = 220


# ── cost model ───────────────────────────────────────────────────────────────

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


# ── v44 backtest engine (v40 core + ADX + funding filter) ───────────────────

class StrictEngineV44:
    def __init__(self, scenario: ScenarioConfig, funding: pd.DataFrame, hourly_prices: pd.Series):
        self.scenario = scenario
        self.funding = funding
        self.hourly_prices = hourly_prices

    def trade_fee(self, qty: float, price: float) -> float:
        return qty * price * self.scenario.fee_rate

    def run(self, df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame, Dict[str, float]]:
        s = self.scenario

        position = 0
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

        for i in range(s.warmup_bars, len(df) - 1):
            t = df.index[i]
            b = df.iloc[i]
            next_bar = df.iloc[i + 1]
            next_time = df.index[i + 1]

            if np.isnan(b["atr"]) or np.isnan(b["ema50"]) or np.isnan(b["ema200"]) or np.isnan(b["ema200_daily"]) or np.isnan(b["adx"]):
                continue

            exited_this_bar = False

            # ── manage position: stop loss ──────────────────────────────────

            if position == 1:
                stop_hit = b["low"] <= stop
                if stop_hit:
                    raw_exit = min(float(stop), float(b["open"]))
                    exit_px = apply_slippage(raw_exit, "long", "exit", s.stop_slippage_bps)
                    funding_pnl = funding_pnl_for_window(
                        self.funding, self.hourly_prices, entry_time, t, "long", qty,
                        s.funding_mode, s.funding_multiplier,
                    )
                    fees = self.trade_fee(qty, entry) + self.trade_fee(qty, exit_px)
                    pnl = qty * (exit_px - entry) - fees + funding_pnl
                    realized += pnl
                    funding_total += funding_pnl
                    fee_total += fees
                    trades.append({
                        "signal_time": signal_time,
                        "entry_time": entry_time,
                        "exit_time": t,
                        "dir": "long",
                        "entry": entry,
                        "exit": exit_px,
                        "qty": qty,
                        "gross_pnl": qty * (exit_px - entry),
                        "fees": -fees,
                        "funding": funding_pnl,
                        "pnl": pnl,
                        "hold_hours": (t - entry_time).total_seconds() / 3600,
                        "reason": "stop",
                    })
                    position, qty, entry, entry_time, signal_time, stop = 0, 0.0, 0.0, None, None, np.nan
                    exited_this_bar = True

            elif position == -1:
                stop_hit = b["high"] >= stop
                if stop_hit:
                    raw_exit = max(float(stop), float(b["open"]))
                    exit_px = apply_slippage(raw_exit, "short", "exit", s.stop_slippage_bps)
                    funding_pnl = funding_pnl_for_window(
                        self.funding, self.hourly_prices, entry_time, t, "short", qty,
                        s.funding_mode, s.funding_multiplier,
                    )
                    fees = self.trade_fee(qty, entry) + self.trade_fee(qty, exit_px)
                    pnl = qty * (entry - exit_px) - fees + funding_pnl
                    realized += pnl
                    funding_total += funding_pnl
                    fee_total += fees
                    trades.append({
                        "signal_time": signal_time,
                        "entry_time": entry_time,
                        "exit_time": t,
                        "dir": "short",
                        "entry": entry,
                        "exit": exit_px,
                        "qty": qty,
                        "gross_pnl": qty * (entry - exit_px),
                        "fees": -fees,
                        "funding": funding_pnl,
                        "pnl": pnl,
                        "hold_hours": (t - entry_time).total_seconds() / 3600,
                        "reason": "stop",
                    })
                    position, qty, entry, entry_time, signal_time, stop = 0, 0.0, 0.0, None, None, np.nan
                    exited_this_bar = True

            # ── manage position: signal exit ────────────────────────────────

            if not exited_this_bar and position == 1 and bool(b["long_exit_signal"]):
                exit_px = apply_slippage(float(next_bar["open"]), "long", "exit", s.exit_slippage_bps)
                funding_pnl = funding_pnl_for_window(
                    self.funding, self.hourly_prices, entry_time, next_time, "long", qty,
                    s.funding_mode, s.funding_multiplier,
                )
                fees = self.trade_fee(qty, entry) + self.trade_fee(qty, exit_px)
                pnl = qty * (exit_px - entry) - fees + funding_pnl
                realized += pnl
                funding_total += funding_pnl
                fee_total += fees
                trades.append({
                    "signal_time": signal_time,
                    "entry_time": entry_time,
                    "exit_time": next_time,
                    "dir": "long",
                    "entry": entry,
                    "exit": exit_px,
                    "qty": qty,
                    "gross_pnl": qty * (exit_px - entry),
                    "fees": -fees,
                    "funding": funding_pnl,
                    "pnl": pnl,
                    "hold_hours": (next_time - entry_time).total_seconds() / 3600,
                    "reason": "ema_inverse_next_open",
                })
                position, qty, entry, entry_time, signal_time, stop = 0, 0.0, 0.0, None, None, np.nan

            elif not exited_this_bar and position == -1 and bool(b["short_exit_signal"]):
                exit_px = apply_slippage(float(next_bar["open"]), "short", "exit", s.exit_slippage_bps)
                funding_pnl = funding_pnl_for_window(
                    self.funding, self.hourly_prices, entry_time, next_time, "short", qty,
                    s.funding_mode, s.funding_multiplier,
                )
                fees = self.trade_fee(qty, entry) + self.trade_fee(qty, exit_px)
                pnl = qty * (entry - exit_px) - fees + funding_pnl
                realized += pnl
                funding_total += funding_pnl
                fee_total += fees
                trades.append({
                    "signal_time": signal_time,
                    "entry_time": entry_time,
                    "exit_time": next_time,
                    "dir": "short",
                    "entry": entry,
                    "exit": exit_px,
                    "qty": qty,
                    "gross_pnl": qty * (entry - exit_px),
                    "fees": -fees,
                    "funding": funding_pnl,
                    "pnl": pnl,
                    "hold_hours": (next_time - entry_time).total_seconds() / 3600,
                    "reason": "ema_inverse_next_open",
                })
                position, qty, entry, entry_time, signal_time, stop = 0, 0.0, 0.0, None, None, np.nan

            # ── entry logic: v44 = v40 + ADX filter + funding filter ────────

            if position == 0:
                equity_now = s.initial_capital + realized
                risk_usd = equity_now * s.risk_pct
                atr_dist = s.stop_atr_mult * float(b["atr"])

                # v44 entry filters
                adx_ok = bool(b["adx_ok"])
                funding_ok_long = bool(b.get("funding_ok_long", True))
                funding_ok_short = bool(b.get("funding_ok_short", True))

                if bool(b["long_signal"]) and atr_dist > 0 and adx_ok and funding_ok_long:
                    raw_entry = float(next_bar["open"])
                    entry_px = apply_slippage(raw_entry, "long", "entry", s.entry_slippage_bps)
                    stop_px = entry_px - atr_dist
                    q = risk_usd / atr_dist
                    if q > 0:
                        position, qty, entry, entry_time, signal_time, stop = 1, float(q), entry_px, next_time, t, float(stop_px)

                elif bool(b["short_signal"]) and atr_dist > 0 and adx_ok and funding_ok_short:
                    raw_entry = float(next_bar["open"])
                    entry_px = apply_slippage(raw_entry, "short", "entry", s.entry_slippage_bps)
                    stop_px = entry_px + atr_dist
                    q = risk_usd / atr_dist
                    if q > 0:
                        position, qty, entry, entry_time, signal_time, stop = -1, float(q), entry_px, next_time, t, float(stop_px)

            # ── equity tracking ──────────────────────────────────────────

            open_pnl = 0.0
            if position == 1:
                open_pnl = qty * (b["close"] - entry)
            elif position == -1:
                open_pnl = qty * (entry - b["close"])
            equity_rows.append({"time": t, "equity": s.initial_capital + realized + open_pnl})

        # ── close final position at end of data ──────────────────────────

        if position != 0 and entry_time is not None:
            t = df.index[-1]
            px = float(df.iloc[-1]["close"])
            exit_px = apply_slippage(px, "long" if position == 1 else "short", "exit", s.exit_slippage_bps)
            side = "long" if position == 1 else "short"
            gross_pnl = qty * (exit_px - entry) if position == 1 else qty * (entry - exit_px)
            funding_pnl = funding_pnl_for_window(
                self.funding, self.hourly_prices, entry_time, t, side, qty,
                s.funding_mode, s.funding_multiplier,
            )
            fees = self.trade_fee(qty, entry) + self.trade_fee(qty, exit_px)
            pnl = gross_pnl - fees + funding_pnl
            funding_total += funding_pnl
            fee_total += fees
            trades.append({
                "signal_time": signal_time,
                "entry_time": entry_time,
                "exit_time": t,
                "dir": side,
                "entry": entry,
                "exit": exit_px,
                "qty": qty,
                "gross_pnl": gross_pnl,
                "fees": -fees,
                "funding": funding_pnl,
                "pnl": pnl,
                "hold_hours": (t - entry_time).total_seconds() / 3600,
                "reason": "eod",
            })

        trades_df = pd.DataFrame(trades)
        eq_df = pd.DataFrame(equity_rows).set_index("time") if equity_rows else pd.DataFrame(columns=["equity"])
        cost_breakdown = {"fees_total": round(float(fee_total), 2), "funding_total": round(float(funding_total), 2)}
        return trades_df, eq_df, cost_breakdown


# ── metrics ──────────────────────────────────────────────────────────────────

def sortino_from_equity(eq: pd.Series) -> float:
    rets = eq.pct_change().dropna()
    if rets.empty:
        return 0.0
    downside = rets[rets < 0]
    if downside.std() == 0 or pd.isna(downside.std()):
        return 0.0
    return float(rets.mean() / downside.std() * np.sqrt(2190))


def metrics(trades: pd.DataFrame, eq: pd.DataFrame, initial_capital: float) -> Dict[str, float]:
    if eq.empty:
        return {k: 0.0 for k in ["return_pct", "cagr", "max_dd", "pf", "wr", "trades", "sharpe", "sortino"]}

    eq_s = eq["equity"].dropna()
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
        pf = 0.0
        wr = 0.0
    else:
        pnls = trades["pnl"]
        wins = pnls[pnls > 0]
        losses = pnls[pnls <= 0]
        wr = float((pnls > 0).mean() * 100)
        pf = float(wins.sum() / abs(losses.sum())) if len(losses) and abs(losses.sum()) > 0 else math.inf

    return {
        "return_pct": round(float(ret), 2),
        "cagr": round(float(cagr), 2),
        "max_dd": round(max_dd, 2),
        "pf": round(float(pf), 2) if np.isfinite(pf) else float("inf"),
        "wr": round(float(wr), 2),
        "trades": int(len(trades)),
        "sharpe": round(float(sharpe), 3),
        "sortino": round(float(sortino), 3),
    }


def yearly_returns(eq: pd.DataFrame, initial_capital: float) -> pd.DataFrame:
    if eq.empty:
        return pd.DataFrame(columns=["year", "return_pct"])
    yearly = eq["equity"].resample("YS").last().dropna()
    prev = yearly.shift(1)
    if len(prev) > 0:
        prev.iloc[0] = initial_capital
    out = ((yearly / prev - 1) * 100).to_frame("return_pct")
    out.index = out.index.year
    out.index.name = "year"
    return out.reset_index()


def trade_distribution(trades: pd.DataFrame) -> Dict[str, float]:
    if trades.empty:
        return {
            "avg_trade_pnl": 0.0, "median_trade_pnl": 0.0,
            "avg_hold_hours": 0.0, "median_hold_hours": 0.0,
        }
    return {
        "avg_trade_pnl": round(float(trades["pnl"].mean()), 2),
        "median_trade_pnl": round(float(trades["pnl"].median()), 2),
        "avg_hold_hours": round(float(trades["hold_hours"].mean()), 2),
        "median_hold_hours": round(float(trades["hold_hours"].median()), 2),
    }


def liquidity_stats(trades: pd.DataFrame, df_4h: pd.DataFrame) -> Dict[str, float]:
    if trades.empty:
        return {"max_qty_pct_of_bar_volume": 0.0, "p95_qty_pct_of_bar_volume": 0.0}
    volume = df_4h["volume"].rename("bar_volume")
    merged = trades.merge(volume, left_on="entry_time", right_index=True, how="left")
    pct = merged["qty"] / merged["bar_volume"] * 100
    return {
        "max_qty_pct_of_bar_volume": round(float(pct.max()), 6),
        "p95_qty_pct_of_bar_volume": round(float(pct.quantile(0.95)), 6),
    }


def reason_breakdown(trades: pd.DataFrame) -> Dict[str, int]:
    if trades.empty or "reason" not in trades.columns:
        return {}
    return {k: int(v) for k, v in trades["reason"].value_counts().items()}


def buy_hold_with_funding(
    df_4h: pd.DataFrame,
    funding: pd.DataFrame,
    hourly_prices: pd.Series,
    fee_rate: float,
    initial_capital: float,
) -> Dict[str, float]:
    start_t = df_4h.index[1]
    end_t = df_4h.index[-1]
    entry_px = float(df_4h.iloc[1]["open"]) * (1 + fee_rate)
    qty = initial_capital / entry_px
    exit_px = float(df_4h.iloc[-1]["close"]) * (1 - fee_rate)
    fees = qty * entry_px * fee_rate + qty * exit_px * fee_rate
    funding_pnl = funding_pnl_for_window(funding, hourly_prices, start_t, end_t, "long", qty, "actual_signed", 1.0)
    final_equity = initial_capital + qty * (exit_px - entry_px) - fees + funding_pnl
    ret = (final_equity / initial_capital - 1) * 100
    years = (end_t - start_t).total_seconds() / (365.25 * 86400)
    cagr = ((final_equity / initial_capital) ** (1 / years) - 1) * 100 if years > 0 else 0.0
    eq = (df_4h["close"] / entry_px) * initial_capital
    dd = (eq / eq.cummax() - 1) * 100
    return {
        "return_pct": round(float(ret), 2),
        "cagr": round(float(cagr), 2),
        "max_dd": round(float(dd.min()), 2),
        "funding_pnl": round(float(funding_pnl), 2),
    }


# ── JSON safe conversion ────────────────────────────────────────────────────

def _to_native(obj):
    """Recursively convert numpy types to Python native types."""
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, (np.ndarray,)):
        return _to_native(obj.tolist())
    if isinstance(obj, dict):
        return {k: _to_native(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_native(v) for v in obj]
    return obj


# ── load prior results for comparison ────────────────────────────────────────

def load_v40_results(repo_root: Path) -> Dict[str, object] | None:
    p = repo_root / "docs/backtests/v40_hyperliquid_strict_validation/summary.json"
    if not p.exists():
        return None
    return json.loads(p.read_text())


def load_v43_results(repo_root: Path) -> Dict[str, object] | None:
    p = repo_root / "docs/backtests/v43_hyperliquid_optimized/summary.json"
    if not p.exists():
        return None
    return json.loads(p.read_text())


# ── report generation ────────────────────────────────────────────────────────

def write_report(
    out_dir: Path,
    diagnostics: Dict[str, object],
    scenario_table: pd.DataFrame,
    baseline_buy_hold: Dict[str, float],
    v40_results: Dict[str, object] | None,
    v43_results: Dict[str, object] | None,
    recommendation: str,
    verdict: str,
) -> None:
    headers = list(scenario_table.columns)
    markdown_lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for _, row in scenario_table.iterrows():
        markdown_lines.append("| " + " | ".join(str(row[h]) for h in headers) + " |")

    lines = [
        "# v44 Hyperliquid BTC — Strict Validation",
        "",
        f"**Verdict:** {verdict}",
        "",
        "## v44 Design Changes from v40",
        "",
        "| # | Change | Purpose |",
        "|---|--------|---------|",
        "| 1 | ADX(14) > 20 filter | Avoid sideways chop that bleeds funding |",
        "| 2 | Funding-rate adverse filter | Skip entry when 1h funding > 0.01% OR 8h avg > 0.005% against position |",
        "",
        "**What v44 removes from v43:** trailing stop, max hold time, profit target.",
        "v44 is a minimal filter layer on top of v40, without v43's complexity.",
        "",
        "## Assumptions",
        "- BTC only, 4h execution, daily EMA200 regime filter, EMA50/EMA200 trend logic",
        "- ATR(14) × 3 initial stop, 2% risk per trade",
        "- Reliable test window: longest continuous Hyperliquid 4h segment",
        "- Next-bar-open execution for signal-driven entries/exits",
        "- Stop losses: conservative stop-market handling (worse of stop vs bar open + adverse slippage)",
        "- Hyperliquid taker fees (4.5 bps) on every entry/exit",
        "- Exact Hyperliquid historical hourly funding applied",
        "- Daily EMA200 regime uses previous completed daily bar (1-day shift, no look-ahead)",
        "- Funding filter uses latest available 1h rate and trailing 8h average at bar close",
        "- NO trailing stop, NO max hold time, NO profit target",
        "",
        "## Scenario Table",
        *markdown_lines,
        "",
    ]

    # ── v40 vs v43 vs v44 comparison ──
    lines.append("## v40 vs v43 vs v44 Comparison (Baseline Scenario)")
    lines.append("")
    lines.append("| Metric | v40 Baseline | v43 Baseline | v44 Baseline |")
    lines.append("|--------|-------------|-------------|-------------|")

    v40_scenarios = v40_results.get("scenarios", []) if v40_results else []
    v43_scenarios = v43_results.get("scenarios", []) if v43_results else []
    v40_baseline = next((r for r in v40_scenarios if r.get("scenario") == "baseline_realistic"), None)
    v43_baseline = next((r for r in v43_scenarios if r.get("scenario") == "baseline_realistic"), None)
    v44_baseline = scenario_table[scenario_table["scenario"] == "baseline_realistic"].iloc[0] if len(scenario_table[scenario_table["scenario"] == "baseline_realistic"]) > 0 else None

    compare_metrics = ["return_pct", "cagr", "max_dd", "pf", "wr", "trades", "sharpe", "sortino", "fees_total", "funding_total"]
    for m in compare_metrics:
        v40v = v40_baseline.get(m, "N/A") if v40_baseline else "N/A"
        v43v = v43_baseline.get(m, "N/A") if v43_baseline else "N/A"
        v44v = v44_baseline.get(m, "N/A") if v44_baseline is not None else "N/A"
        lines.append(f"| {m} | {v40v} | {v43v} | {v44v} |")
    lines.append("")

    # ── v40 vs v43 vs v44 stressed ──
    lines.append("## Stressed Scenario Comparison")
    lines.append("")
    lines.append("| Metric | v40 Stressed | v43 Stressed | v44 Stressed |")
    lines.append("|--------|-------------|-------------|-------------|")

    v40_stressed = next((r for r in v40_scenarios if r.get("scenario") == "stressed_conservative"), None)
    v43_stressed = next((r for r in v43_scenarios if r.get("scenario") == "stressed_conservative"), None)
    v44_stressed = scenario_table[scenario_table["scenario"] == "stressed_conservative"].iloc[0] if len(scenario_table[scenario_table["scenario"] == "stressed_conservative"]) > 0 else None

    for m in compare_metrics:
        v40v = v40_stressed.get(m, "N/A") if v40_stressed else "N/A"
        v43v = v43_stressed.get(m, "N/A") if v43_stressed else "N/A"
        v44v = v44_stressed.get(m, "N/A") if v44_stressed is not None else "N/A"
        lines.append(f"| {m} | {v40v} | {v43v} | {v44v} |")
    lines.append("")

    lines.extend([
        "## Baseline Passive",
        f"- Passive long buy-and-hold on same window: return {baseline_buy_hold['return_pct']}%, CAGR {baseline_buy_hold['cagr']}%, max drawdown {baseline_buy_hold['max_dd']}%, funding pnl {baseline_buy_hold['funding_pnl']}.",
        "",
        "## Methodology Notes",
        "- **Design changes**: 2 filters over v40 (ADX + funding), NO trailing/PT/max-hold.",
        "- **No parameter optimization**: v44 uses v40 base params plus fixed thresholds (ADX>20, funding 0.01%/0.005%).",
        "- **Reason tracking**: Each trade tagged with exit reason (stop, ema_inverse_next_open, eod).",
        "",
        "## Recommendation",
        recommendation,
        "",
        "## Diagnostics",
        "```json",
        json.dumps(_to_native(diagnostics), indent=2),
        "```",
    ])
    (out_dir / "report.md").write_text("\n".join(lines))


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    out_dir = repo_root / "docs/backtests/v44_hyperliquid_optimized"
    out_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print("  v44 Hyperliquid BTC — Strict Validation")
    print("  = v40 + ADX(14)>20 + Funding filter")
    print("  NO trailing stop, NO max hold, NO profit target")
    print("=" * 70)

    # ── fetch data ────────────────────────────────────────────────────────
    print("\n[1/5] Fetching Hyperliquid 4h and 1d OHLCV...")
    end_ms = now_floor_4h_ms() - 1
    earliest_hl_4h = infer_earliest_hyperliquid_4h("BTC")
    start_hl_4h_ms = int(earliest_hl_4h.timestamp() * 1000)
    start_daily_warmup_ms = dt_to_ms("2021-01-01")

    hyper_4h = fetch_hyperliquid_klines("BTC", "4h", start_hl_4h_ms, end_ms, chunk_days=120)
    hyper_1d = fetch_hyperliquid_klines("BTC", "1d", start_daily_warmup_ms, end_ms, chunk_days=365)

    # ── reliable window ───────────────────────────────────────────────────
    print("[2/5] Computing reliable continuous window...")
    reliable_start, reliable_end, reliable_bars = longest_continuous_segment(hyper_4h, "4h")
    strict_4h = hyper_4h.loc[(hyper_4h.index >= reliable_start) & (hyper_4h.index <= reliable_end)].copy()
    strict_1d = hyper_1d.loc[hyper_1d.index <= reliable_end].copy()
    print(f"      Reliable 4h window: {reliable_start} → {reliable_end} ({reliable_bars} bars)")

    # ── funding ───────────────────────────────────────────────────────────
    print("[3/5] Fetching funding history...")
    funding = fetch_funding_history("BTC", int(reliable_start.timestamp() * 1000), int(reliable_end.timestamp() * 1000) + 1)
    print(f"      Funding records: {len(funding)}")

    # ── build features ────────────────────────────────────────────────────
    print("[4/5] Building v44 features (v40 + ADX + funding filters)...")
    features = build_features_v44(strict_4h, strict_1d, funding)
    hourly_prices = strict_4h["close"].reindex(funding.index, method="ffill")

    # ── filter coverage ───────────────────────────────────────────────────
    adx_pct = float(features["adx_ok"].mean() * 100)
    funding_ok_long_pct = float(features["funding_ok_long"].mean() * 100)
    funding_ok_short_pct = float(features["funding_ok_short"].mean() * 100)
    print(f"      ADX>20: {adx_pct:.1f}% of bars")
    print(f"      Funding-ok-long: {funding_ok_long_pct:.1f}% of bars")
    print(f"      Funding-ok-short: {funding_ok_short_pct:.1f}% of bars")

    # ── scenarios ─────────────────────────────────────────────────────────
    scenarios = [
        ScenarioConfig(
            name="optimistic_plausible",
            fee_rate=0.00045,
            entry_slippage_bps=1.0,
            exit_slippage_bps=1.0,
            stop_slippage_bps=3.0,
            funding_mode="actual_signed",
            funding_multiplier=1.0,
            description="Next-open execution, taker fees, light slippage, exact signed funding — v44 filters active",
        ),
        ScenarioConfig(
            name="baseline_realistic",
            fee_rate=0.00045,
            entry_slippage_bps=3.0,
            exit_slippage_bps=3.0,
            stop_slippage_bps=8.0,
            funding_mode="actual_signed",
            funding_multiplier=1.0,
            description="Next-open execution, taker fees, moderate slippage, exact signed funding — v44 filters active",
        ),
        ScenarioConfig(
            name="stressed_conservative",
            fee_rate=0.00045,
            entry_slippage_bps=6.0,
            exit_slippage_bps=6.0,
            stop_slippage_bps=15.0,
            funding_mode="adverse_only",
            funding_multiplier=1.5,
            description="Next-open execution, taker fees, heavy slippage, stop stress, only adverse funding counted and magnified — v44 filters active",
        ),
    ]

    # ── run backtests ──────────────────────────────────────────────────────
    print("[5/5] Running 3 scenario backtests...")
    scenario_rows: List[dict] = []
    scenario_details: Dict[str, object] = {}

    for scenario in scenarios:
        print(f"\n      ▶ {scenario.name} ({scenario.description[:60]}...)")
        trades, eq, cost_breakdown = StrictEngineV44(scenario, funding, hourly_prices).run(features)
        summary = metrics(trades, eq, scenario.initial_capital)
        dist = trade_distribution(trades)
        liq = liquidity_stats(trades, strict_4h)
        yearly = yearly_returns(eq, scenario.initial_capital)
        reasons = reason_breakdown(trades)

        # save CSVs
        trades.to_csv(out_dir / f"{scenario.name}_trades.csv", index=False)
        eq.to_csv(out_dir / f"{scenario.name}_equity.csv")
        yearly.to_csv(out_dir / f"{scenario.name}_yearly.csv", index=False)
        full_summary = summary | cost_breakdown | dist | liq
        pd.DataFrame([full_summary]).to_csv(out_dir / f"{scenario.name}_summary.csv", index=False)

        scenario_rows.append({
            "scenario": scenario.name,
            "description": scenario.description,
            **summary,
            **cost_breakdown,
        })
        scenario_details[scenario.name] = {
            "config": asdict(scenario),
            "summary": summary,
            "cost_breakdown": cost_breakdown,
            "trade_distribution": dist,
            "liquidity": liq,
            "exit_reasons": reasons,
        }

        print(f"         Return: {summary['return_pct']:+.2f}% | PF: {summary['pf']:.2f} | WR: {summary['wr']:.1f}% | Trades: {summary['trades']} | MaxDD: {summary['max_dd']:.2f}%")
        print(f"         Fees: ${cost_breakdown['fees_total']:.2f} | Funding: ${cost_breakdown['funding_total']:.2f}")

    # ── save global outputs ────────────────────────────────────────────────
    scenario_table = pd.DataFrame(scenario_rows)
    scenario_table.to_csv(out_dir / "scenario_table.csv", index=False)

    buy_hold = buy_hold_with_funding(strict_4h, funding, hourly_prices, 0.00045, 10_000.0)
    v40_results = load_v40_results(repo_root)
    v43_results = load_v43_results(repo_root)

    diagnostics = {
        "generated_at_utc": pd.Timestamp.now(tz="UTC").isoformat(),
        "methodology": {
            "execution": "next 4h bar open for signal-driven entries/exits; intrabar conservative stop-market fill",
            "daily_regime_alignment": "previous completed daily EMA200 shifted by one day before 4h forward-fill",
            "funding": "exact Hyperliquid hourly fundingHistory for BTC; filter uses latest 1h rate + 8h trailing average at bar close",
            "adx": "ADX(14) with Wilder's smoothing; entry allowed only when > 20",
            "no_trailing_stop": True,
            "no_max_hold_time": True,
            "no_profit_target": True,
            "reliable_window_only": True,
        },
        "v44_filters": {
            "adx_threshold": 20.0,
            "funding_1h_threshold": 0.0001,
            "funding_8h_threshold": 0.00005,
        },
        "filter_coverage": {
            "adx_ok_pct": round(adx_pct, 2),
            "funding_ok_long_pct": round(funding_ok_long_pct, 2),
            "funding_ok_short_pct": round(funding_ok_short_pct, 2),
        },
        "data_integrity": {
            "hyperliquid_4h_full": missing_bar_stats(hyper_4h, "4h"),
            "reliable_continuous_4h_segment": {
                "start": reliable_start.isoformat(),
                "end": reliable_end.isoformat(),
                "bars": reliable_bars,
            },
            "strict_4h_window": missing_bar_stats(strict_4h, "4h"),
            "strict_1d_window": missing_bar_stats(strict_1d, "1D"),
            "funding_window": missing_bar_stats(
                funding.set_index(funding.index.floor("1h"))[~funding.set_index(funding.index.floor("1h")).index.duplicated(keep="last")],
                "1h",
            ),
        },
        "buy_hold_overlap_same_window": buy_hold,
        "scenario_details": scenario_details,
    }
    (out_dir / "diagnostics.json").write_text(json.dumps(_to_native(diagnostics), indent=2))

    summary_json = {
        "version": "v44",
        "scenarios": scenario_rows,
        "buy_hold": buy_hold,
        "v40_comparison_available": v40_results is not None,
        "v43_comparison_available": v43_results is not None,
    }
    (out_dir / "summary.json").write_text(json.dumps(_to_native(summary_json), indent=2))

    # ── verdict ────────────────────────────────────────────────────────────
    baseline = next(row for row in scenario_rows if row["scenario"] == "baseline_realistic")
    stressed = next(row for row in scenario_rows if row["scenario"] == "stressed_conservative")

    if stressed["return_pct"] <= 0 or stressed["pf"] < 1.0 or baseline["return_pct"] <= 0:
        verdict = "NO-GO"
        recommendation = (
            "The v44 edge does not survive conservative execution stress cleanly enough for live deployment. "
            "Use this only for testnet validation of the plumbing, and do not move to live until the strategy is reworked or materially improved."
        )
    elif baseline["pf"] < 1.2 or baseline["max_dd"] < -20 or baseline["cagr"] < buy_hold["cagr"] * 0.4:
        verdict = "CONDITIONAL"
        recommendation = (
            "The v44 strategy survives realistic execution, but the edge is modest and it badly underperforms passive long BTC on the same window. "
            "That means this is suitable for testnet validation and, at most, extremely tiny live sizing while you validate real slippage, stop fills, and funding drift."
        )
    else:
        verdict = "GO"
        recommendation = (
            "The v44 strategy survives strict realism well enough to justify a tiny live pilot, "
            "but only with ongoing monitoring of slippage, stop execution, and funding drift."
        )

    write_report(out_dir, diagnostics, scenario_table[["scenario", "return_pct", "cagr", "max_dd", "pf", "wr", "trades", "sharpe", "sortino", "fees_total", "funding_total"]], buy_hold, v40_results, v43_results, recommendation, verdict)

    # ── console summary ────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("  RESULTS SUMMARY")
    print("=" * 70)
    print(f"  Verdict: {verdict}")
    print(f"  Optimistic return: {scenario_rows[0]['return_pct']:+.2f}%  |  PF: {scenario_rows[0]['pf']:.2f}  |  Trades: {scenario_rows[0]['trades']}")
    print(f"  Baseline  return: {baseline['return_pct']:+.2f}%  |  PF: {baseline['pf']:.2f}  |  MaxDD: {baseline['max_dd']:.2f}%")
    print(f"  Stressed  return: {stressed['return_pct']:+.2f}%  |  PF: {stressed['pf']:.2f}  |  MaxDD: {stressed['max_dd']:.2f}%")
    print(f"  Buy&Hold  return: {buy_hold['return_pct']:+.2f}%  |  MaxDD: {buy_hold['max_dd']:.2f}%")
    print(f"  Output: {out_dir}")

    # ── v40 vs v43 vs v44 comparison ────────────────────────────────────────
    print("\n" + "=" * 70)
    print("  v40 vs v43 vs v44 COMPARISON (Baseline)")
    print("=" * 70)

    v40_scenarios = v40_results.get("scenarios", []) if v40_results else []
    v43_scenarios = v43_results.get("scenarios", []) if v43_results else []
    v40_bl = next((r for r in v40_scenarios if r.get("scenario") == "baseline_realistic"), None)
    v43_bl = next((r for r in v43_scenarios if r.get("scenario") == "baseline_realistic"), None)
    v44_bl = baseline

    compare_metrics_list = ["return_pct", "cagr", "max_dd", "pf", "wr", "trades", "sharpe", "sortino", "fees_total", "funding_total"]

    print(f"\n  {'Metric':<20} {'v40':>10} {'v43':>10} {'v44':>10}")
    print(f"  {'-'*55}")
    for m in compare_metrics_list:
        v40v = v40_bl.get(m, 0) if v40_bl else 0
        v43v = v43_bl.get(m, 0) if v43_bl else 0
        v44v = v44_bl.get(m, 0) if v44_bl else 0
        print(f"  {m:<20} {str(v40v):>10} {str(v43v):>10} {str(v44v):>10}")

    print(f"\n  All outputs saved to: {out_dir}")
    print(f"  Files: scenario_table.csv, *_trades.csv, *_equity.csv, *_yearly.csv, *_summary.csv, summary.json, diagnostics.json, report.md")


if __name__ == "__main__":
    main()
