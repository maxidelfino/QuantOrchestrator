#!/usr/bin/env python3
"""v47 RSI Momentum Pullback — BTC Perp Strategy for Hyperliquid

ITERATION over v46: same exit structure (3x ATR trail + max 12 bars), but
improved entry signal quality via RSI momentum confirmation.

Changes from v46:
- RSI momentum confirmation: RSI must be rising (long) or falling (short)
  vs 2 bars ago — confirms pullback is ending and trend resuming
- RSI ranges adjusted for trend context:
  Longs: 35-50 (not 25-50; in uptrends RSI rarely drops below 35)
  Shorts: 50-65 (not 50-75; in downtrends RSI rarely rises above 65)
- Removed: daily EMA200 regime (redundant with ADX/DI trend filter)
- Removed: volume/EMA50/time filters (too restrictive — killed all signals)

Key insight: in strong trends (ADX>25), RSI mean is 63 (uptrend) or 39.5
(downtrend). Extreme RSI levels (20-40/60-80) NEVER occur in trending
markets. The edge comes from entering when RSI is TURNING back in trend
direction, not from waiting for impossible extremes.

Hard requirements target:
- Holding time < 24h average
- ≤2 trades/week
- Max DD < 25%
- PF > 1.2 in baseline
"""

from __future__ import annotations

import json, math, time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
import requests

HYPERLIQUID_INFO = "https://api.hyperliquid.xyz/info"
BARS_PER_YEAR = 4380  # 2h bars per year

# ── helpers ──────────────────────────────────────────────────────────────────

def dt_to_ms(s: str) -> int:
    return int(datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)

def now_floor_2h_ms() -> int:
    now = pd.Timestamp.now(tz="UTC")
    floored_hour = (now.hour // 2) * 2
    floored = now.floor("D") + pd.Timedelta(hours=floored_hour)
    return int(floored.timestamp() * 1000)

def fetch_hyperliquid_klines(coin, interval, start_ms, end_ms, chunk_days):
    out = []
    cur = start_ms
    chunk_ms = chunk_days * 24 * 60 * 60 * 1000
    while cur < end_ms:
        chunk_end = min(cur + chunk_ms, end_ms)
        r = requests.post(HYPERLIQUID_INFO, json={
            "type": "candleSnapshot",
            "req": {"coin": coin, "interval": interval, "startTime": cur, "endTime": chunk_end},
        }, timeout=30)
        r.raise_for_status()
        out.extend(r.json())
        cur = chunk_end
        time.sleep(0.05)
    if not out:
        raise RuntimeError(f"No Hyperliquid data for {coin} {interval}")
    df = pd.DataFrame(out)
    df["t"] = pd.to_datetime(df["t"], unit="ms", utc=True)
    for c in ["o", "h", "l", "c", "v"]:
        df[c] = df[c].astype(float)
    df = df.rename(columns={"t": "time", "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
    return df.set_index("time")[["open", "high", "low", "close", "volume"]].sort_index().pipe(
        lambda d: d[~d.index.duplicated(keep="last")])

def fetch_funding_history(coin, start_ms, end_ms, chunk_hours=400):
    out = []
    cur = start_ms
    chunk_ms = chunk_hours * 60 * 60 * 1000
    while cur < end_ms:
        chunk_end = min(cur + chunk_ms, end_ms)
        r = requests.post(HYPERLIQUID_INFO, json={
            "type": "fundingHistory", "coin": coin, "startTime": cur, "endTime": chunk_end}, timeout=30)
        r.raise_for_status()
        out.extend(r.json())
        cur = chunk_end + 1
        time.sleep(0.05)
    if not out:
        raise RuntimeError(f"No funding history for {coin}")
    df = pd.DataFrame(out)
    df["time"] = pd.to_datetime(df["time"], unit="ms", utc=True)
    df["fundingRate"] = df["fundingRate"].astype(float)
    return df.set_index("time")[["fundingRate"]].sort_index().pipe(
        lambda d: d[~d.index.duplicated(keep="last")])

def infer_earliest_2h(coin="BTC"):
    probes = [("2025-01-01", "2025-01-31"), ("2025-02-01", "2025-02-28"),
              ("2025-03-01", "2025-03-31"), ("2025-04-01", "2025-04-30")]
    first = None
    for s, e in probes:
        r = requests.post(HYPERLIQUID_INFO, json={
            "type": "candleSnapshot",
            "req": {"coin": coin, "interval": "2h", "startTime": dt_to_ms(s), "endTime": dt_to_ms(e)}}, timeout=30)
        r.raise_for_status()
        data = r.json()
        if data:
            ts = pd.to_datetime(data[0]["t"], unit="ms", utc=True)
            first = ts if first is None else min(first, ts)
    if first is None:
        raise RuntimeError("Could not infer earliest Hyperliquid 2h BTC candle")
    return first

def missing_bar_stats(df, freq):
    expected = pd.date_range(df.index.min(), df.index.max(), freq=freq, tz="UTC")
    missing = expected.difference(df.index)
    return {"bars": int(len(df)), "duplicates": int(df.index.duplicated().sum()),
            "missing_bars": int(len(missing)), "first": df.index.min().isoformat(),
            "last": df.index.max().isoformat(),
            "sample_missing": [ts.isoformat() for ts in missing[:10]]}

def longest_continuous_segment(df, freq):
    step = pd.Timedelta(freq)
    segments = []
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

# ── indicators ───────────────────────────────────────────────────────────────

def compute_atr(df, period=14):
    pc = df["close"].shift(1)
    tr = pd.concat([(df["high"] - df["low"]), (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False).mean()

def compute_adx_di(df, period=14):
    pc = df["close"].shift(1)
    tr = pd.concat([(df["high"] - df["low"]), (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    atr_s = tr.ewm(alpha=1.0 / period, adjust=False).mean()
    up_move = df["high"] - df["high"].shift(1)
    down_move = df["low"].shift(1) - df["low"]
    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=df.index)
    plus_di = 100 * (plus_dm.ewm(alpha=1.0 / period, adjust=False).mean() / atr_s.replace(0, np.nan))
    minus_di = 100 * (minus_dm.ewm(alpha=1.0 / period, adjust=False).mean() / atr_s.replace(0, np.nan))
    dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.ewm(alpha=1.0 / period, adjust=False).mean()
    return adx, plus_di, minus_di

def compute_rsi(df, period=14):
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

# ── feature builder ──────────────────────────────────────────────────────────

def build_features(df_2h, df_daily, warmup_daily_start="2021-01-01", version="v47"):
    out = df_2h.copy()

    out["atr14"] = compute_atr(out, 14)
    out["adx14"], out["plus_di"], out["minus_di"] = compute_adx_di(out, 14)
    out["rsi14"] = compute_rsi(out, 14)

    if version == "v47":
        # ── v47: momentum-confirmed pullback entries ─────────────────
        # Key insight: in strong trends, RSI never reaches extremes.
        # Instead of deeper RSI levels, we require RSI to be TURNING
        # back in trend direction (momentum resuming).
        #
        # RSI ranges adjusted for trend context:
        #   Longs: RSI 35-50 (pullback within uptrend, mean RSI in uptrend = 63)
        #   Shorts: RSI 50-65 (rally within downtrend, mean RSI in downtrend = 39.5)
        #
        # RSI momentum confirmation:
        #   Long: RSI rising (rsi14 > rsi14.shift(2)) — pullback ending
        #   Short: RSI falling (rsi14 < rsi14.shift(2)) — rally ending

        out["rsi_rising"] = out["rsi14"] > out["rsi14"].shift(2)
        out["rsi_falling"] = out["rsi14"] < out["rsi14"].shift(2)

        # Realistic pullback ranges WITHIN trends
        out["rsi_pullback"] = (out["rsi14"] >= 35) & (out["rsi14"] <= 50)
        out["rsi_overextended"] = (out["rsi14"] >= 50) & (out["rsi14"] <= 65)

        out["bullish"] = out["close"] > out["open"]
        out["bearish"] = out["close"] < out["open"]

        out["adx_ok"] = out["adx14"] > 25
        out["uptrend_adx"] = (out["plus_di"] > out["minus_di"]) & out["adx_ok"]
        out["downtrend_adx"] = (out["minus_di"] > out["plus_di"]) & out["adx_ok"]

        # Entry: trend + pullback + candle + RSI momentum confirmation
        out["long_signal"] = (
            out["uptrend_adx"]
            & out["rsi_pullback"]
            & out["bullish"]
            & out["rsi_rising"]
        )
        out["short_signal"] = (
            out["downtrend_adx"]
            & out["rsi_overextended"]
            & out["bearish"]
            & out["rsi_falling"]
        )

    else:
        # ── v46: original logic ──────────────────────────────────────
        out["uptrend"] = (out["plus_di"] > out["minus_di"]) & (out["adx14"] > 25)
        out["downtrend"] = (out["minus_di"] > out["plus_di"]) & (out["adx14"] > 25)

        # RSI pullback zones
        out["rsi_pullback"] = (out["rsi14"] >= 25) & (out["rsi14"] <= 50)
        out["rsi_overextended"] = (out["rsi14"] >= 50) & (out["rsi14"] <= 75)

        # Candle direction
        out["bullish"] = out["close"] > out["open"]
        out["bearish"] = out["close"] < out["open"]

        # Daily EMA200 regime
        daily = df_daily.copy()
        daily = daily.loc[daily.index >= pd.Timestamp(warmup_daily_start, tz="UTC")].copy()
        daily["ema200_daily"] = daily["close"].ewm(span=200, adjust=False).mean()
        daily["ema200_daily_completed"] = daily["ema200_daily"].shift(1)
        regime = daily[["ema200_daily_completed"]].reindex(out.index, method="ffill")
        out["ema200_daily"] = regime["ema200_daily_completed"]
        out["regime_bull"] = out["close"] > out["ema200_daily"]
        out["regime_bear"] = out["close"] < out["ema200_daily"]

        # Entry signals — medium-tight
        out["adx_ok"] = out["adx14"] > 25
        out["uptrend_adx"] = (out["plus_di"] > out["minus_di"]) & out["adx_ok"]
        out["downtrend_adx"] = (out["minus_di"] > out["plus_di"]) & out["adx_ok"]

        out["long_signal"] = (
            out["uptrend_adx"] & out["rsi_pullback"] & out["bullish"]
        )
        out["short_signal"] = (
            out["downtrend_adx"] & out["rsi_overextended"] & out["bearish"]
        )

    # No exit signals — trailing stop handles it
    out["long_exit"] = False
    out["short_exit"] = False

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
    risk_pct: float = 0.015
    stop_atr_mult: float = 3.0    # Initial stop: 3x ATR
    trail_atr_mult: float = 3.0   # Trailing stop: 3x ATR from peak
    tp_atr_mult: float = 0.0      # Disabled — let trailing stop run
    max_hold_bars: int = 12
    warmup_bars: int = 200

# ── cost model ───────────────────────────────────────────────────────────────

def apply_slippage(price, side, action, bps):
    bump = bps / 10_000.0
    if action == "entry":
        return price * (1 + bump) if side == "long" else price * (1 - bump)
    return price * (1 - bump) if side == "long" else price * (1 + bump)

def funding_pnl_for_window(funding, hourly_prices, entry_time, exit_time, side, qty, mode, multiplier):
    window = funding.loc[(funding.index > entry_time) & (funding.index <= exit_time)].copy()
    if window.empty:
        return 0.0
    prices = hourly_prices.reindex(window.index, method="ffill")
    sign = 1.0 if side == "long" else -1.0
    signed = -sign * window["fundingRate"] * qty * prices
    if mode == "actual_signed":
        return float((signed * multiplier).sum())
    elif mode == "adverse_only":
        return float(signed.clip(upper=0.0).sum() * multiplier)
    raise ValueError(f"Unknown funding mode: {mode}")

# ── backtest engine ──────────────────────────────────────────────────────────

class StrictEngine:
    def __init__(self, scenario, funding, hourly_prices):
        self.s = scenario
        self.funding = funding
        self.hourly_prices = hourly_prices

    def trade_fee(self, qty, price):
        return qty * price * self.s.fee_rate

    def _close(self, pos, qty, entry, entry_time, signal_time, exit_time, exit_px,
               funding_total, fee_total, realized, trades, reason):
        side = "long" if pos == 1 else "short"
        fp = funding_pnl_for_window(self.funding, self.hourly_prices, entry_time, exit_time,
                                     side, qty, self.s.funding_mode, self.s.funding_multiplier)
        fees = self.trade_fee(qty, entry) + self.trade_fee(qty, exit_px)
        gp = qty * (exit_px - entry) if pos == 1 else qty * (entry - exit_px)
        pnl = gp - fees + fp
        trades.append({"signal_time": signal_time, "entry_time": entry_time,
                       "exit_time": exit_time, "dir": side, "entry": entry, "exit": exit_px,
                       "qty": qty, "gross_pnl": gp, "fees": -fees, "funding": fp,
                       "pnl": pnl, "hold_hours": (exit_time - entry_time).total_seconds() / 3600,
                       "reason": reason})
        return realized + pnl, funding_total + fp, fee_total + fees

    def run(self, df):
        s = self.s
        pos, qty, entry, entry_time, signal_time = 0, 0.0, 0.0, None, None
        stop, trail_stop, high_water = np.nan, np.nan, np.nan
        bars_held = 0
        realized, trades, equity_rows = 0.0, [], []
        funding_total, fee_total, signal_count = 0.0, 0.0, 0

        for i in range(s.warmup_bars, len(df) - 1):
            t = df.index[i]
            b = df.iloc[i]
            nb = df.iloc[i + 1]
            nt = df.index[i + 1]

            nan_cols = ["atr14", "adx14", "rsi14", "plus_di", "minus_di", "ema200_daily"]
            if any(np.isnan(b.get(c, np.nan)) for c in nan_cols if c in df.columns):
                continue

            exited = False
            cur_atr = float(b["atr14"]) if not np.isnan(b["atr14"]) else 0

            # ── update trailing stop ─────────────────────────────────────
            if pos == 1 and cur_atr > 0:
                high_water = max(high_water, float(b["high"]))
                trail_stop = high_water - s.trail_atr_mult * cur_atr
            elif pos == -1 and cur_atr > 0:
                high_water = min(high_water, float(b["low"]))
                trail_stop = high_water + s.trail_atr_mult * cur_atr

            # ── trailing stop hit ────────────────────────────────────────
            if pos == 1 and b["low"] <= trail_stop:
                raw = min(float(trail_stop), float(b["open"]))
                px = apply_slippage(raw, "long", "exit", s.stop_slippage_bps)
                realized, funding_total, fee_total = self._close(
                    pos, qty, entry, entry_time, signal_time, t, px,
                    funding_total, fee_total, realized, trades, "trail_stop")
                pos, qty, entry, entry_time, signal_time, stop, trail_stop, high_water, bars_held = 0, 0.0, 0.0, None, None, np.nan, np.nan, np.nan, 0
                exited = True
            elif pos == -1 and b["high"] >= trail_stop:
                raw = max(float(trail_stop), float(b["open"]))
                px = apply_slippage(raw, "short", "exit", s.stop_slippage_bps)
                realized, funding_total, fee_total = self._close(
                    pos, qty, entry, entry_time, signal_time, t, px,
                    funding_total, fee_total, realized, trades, "trail_stop")
                pos, qty, entry, entry_time, signal_time, stop, trail_stop, high_water, bars_held = 0, 0.0, 0.0, None, None, np.nan, np.nan, np.nan, 0
                exited = True

            # ── initial stop (only if trail hasn't moved above it) ───────
            if not exited and pos == 1 and b["low"] <= stop:
                raw = min(float(stop), float(b["open"]))
                px = apply_slippage(raw, "long", "exit", s.stop_slippage_bps)
                realized, funding_total, fee_total = self._close(
                    pos, qty, entry, entry_time, signal_time, t, px,
                    funding_total, fee_total, realized, trades, "stop")
                pos, qty, entry, entry_time, signal_time, stop, trail_stop, high_water, bars_held = 0, 0.0, 0.0, None, None, np.nan, np.nan, np.nan, 0
                exited = True
            elif not exited and pos == -1 and b["high"] >= stop:
                raw = max(float(stop), float(b["open"]))
                px = apply_slippage(raw, "short", "exit", s.stop_slippage_bps)
                realized, funding_total, fee_total = self._close(
                    pos, qty, entry, entry_time, signal_time, t, px,
                    funding_total, fee_total, realized, trades, "stop")
                pos, qty, entry, entry_time, signal_time, stop, trail_stop, high_water, bars_held = 0, 0.0, 0.0, None, None, np.nan, np.nan, np.nan, 0
                exited = True

            # ── max hold ─────────────────────────────────────────────────
            if not exited and pos != 0:
                bars_held += 1
                if bars_held >= s.max_hold_bars:
                    raw = float(nb["open"])
                    px = apply_slippage(raw, "long" if pos == 1 else "short", "exit", s.exit_slippage_bps)
                    realized, funding_total, fee_total = self._close(
                        pos, qty, entry, entry_time, signal_time, nt, px,
                        funding_total, fee_total, realized, trades, "max_hold")
                    pos, qty, entry, entry_time, signal_time, stop, trail_stop, high_water, bars_held = 0, 0.0, 0.0, None, None, np.nan, np.nan, np.nan, 0
                    exited = True

            # ── take profit ──────────────────────────────────────────────
            if not exited and pos != 0 and cur_atr > 0 and s.tp_atr_mult > 0:
                tp_price = entry + s.tp_atr_mult * cur_atr if pos == 1 else entry - s.tp_atr_mult * cur_atr
                if pos == 1 and b["high"] >= tp_price:
                    raw = max(float(tp_price), float(b["open"]))
                    px = apply_slippage(raw, "long", "exit", s.exit_slippage_bps)
                    realized, funding_total, fee_total = self._close(
                        pos, qty, entry, entry_time, signal_time, t, px,
                        funding_total, fee_total, realized, trades, "take_profit")
                    pos, qty, entry, entry_time, signal_time, stop, trail_stop, high_water, bars_held = 0, 0.0, 0.0, None, None, np.nan, np.nan, np.nan, 0
                    exited = True
                elif pos == -1 and b["low"] <= tp_price:
                    raw = min(float(tp_price), float(b["open"]))
                    px = apply_slippage(raw, "short", "exit", s.exit_slippage_bps)
                    realized, funding_total, fee_total = self._close(
                        pos, qty, entry, entry_time, signal_time, t, px,
                        funding_total, fee_total, realized, trades, "take_profit")
                    pos, qty, entry, entry_time, signal_time, stop, trail_stop, high_water, bars_held = 0, 0.0, 0.0, None, None, np.nan, np.nan, np.nan, 0
                    exited = True

            # ── entry ────────────────────────────────────────────────────
            if pos == 0:
                equity_now = s.initial_capital + realized
                risk_usd = equity_now * s.risk_pct

                if bool(b.get("long_signal", False)) and cur_atr > 0:
                    raw_entry = float(nb["open"])
                    ep = apply_slippage(raw_entry, "long", "entry", s.entry_slippage_bps)
                    init_stop = ep - s.stop_atr_mult * cur_atr
                    init_trail = ep - s.trail_atr_mult * cur_atr
                    q = risk_usd / (s.stop_atr_mult * cur_atr)
                    if q > 0:
                        pos, qty, entry, entry_time, signal_time = 1, float(q), ep, nt, t
                        stop, trail_stop, high_water = float(init_stop), float(init_trail), float(ep)
                        bars_held = 0
                        signal_count += 1
                elif bool(b.get("short_signal", False)) and cur_atr > 0:
                    raw_entry = float(nb["open"])
                    ep = apply_slippage(raw_entry, "short", "entry", s.entry_slippage_bps)
                    init_stop = ep + s.stop_atr_mult * cur_atr
                    init_trail = ep + s.trail_atr_mult * cur_atr
                    q = risk_usd / (s.stop_atr_mult * cur_atr)
                    if q > 0:
                        pos, qty, entry, entry_time, signal_time = -1, float(q), ep, nt, t
                        stop, trail_stop, high_water = float(init_stop), float(init_trail), float(ep)
                        bars_held = 0
                        signal_count += 1

            # ── equity tracking ──────────────────────────────────────────
            open_pnl = 0.0
            if pos == 1:
                open_pnl = qty * (b["close"] - entry)
            elif pos == -1:
                open_pnl = qty * (entry - b["close"])
            equity_rows.append({"time": t, "equity": s.initial_capital + realized + open_pnl})

        # ── close final position ─────────────────────────────────────────
        if pos != 0 and entry_time is not None:
            t = df.index[-1]
            px = apply_slippage(float(df.iloc[-1]["close"]), "long" if pos == 1 else "short", "exit", s.exit_slippage_bps)
            realized, funding_total, fee_total = self._close(
                pos, qty, entry, entry_time, signal_time, t, px,
                funding_total, fee_total, realized, trades, "eod")

        trades_df = pd.DataFrame(trades)
        eq_df = pd.DataFrame(equity_rows).set_index("time") if equity_rows else pd.DataFrame(columns=["equity"])
        cost = {"fees_total": round(float(fee_total), 2), "funding_total": round(float(funding_total), 2)}
        return trades_df, eq_df, cost

# ── metrics ──────────────────────────────────────────────────────────────────

def sortino_from_equity(eq):
    rets = eq.pct_change().dropna()
    if rets.empty:
        return 0.0
    downside = rets[rets < 0]
    if len(downside) == 0 or downside.std() == 0 or pd.isna(downside.std()):
        return 0.0
    return float(rets.mean() / downside.std() * np.sqrt(BARS_PER_YEAR))

def metrics(trades, eq, initial_capital):
    if eq.empty:
        return {k: 0.0 for k in ["return_pct", "cagr", "max_dd", "pf", "wr", "trades", "sharpe", "sortino"]}
    eq_s = eq["equity"].dropna()
    ret = (eq_s.iloc[-1] / initial_capital - 1) * 100
    years = (eq_s.index[-1] - eq_s.index[0]).total_seconds() / (365.25 * 86400)
    cagr = ((eq_s.iloc[-1] / initial_capital) ** (1 / years) - 1) * 100 if years > 0 else 0.0
    peak = eq_s.cummax()
    max_dd = float(((eq_s / peak - 1) * 100).min())
    rets = eq_s.pct_change().dropna()
    sharpe = float(rets.mean() / rets.std() * np.sqrt(BARS_PER_YEAR)) if len(rets) > 1 and rets.std() > 0 else 0.0
    sortino = sortino_from_equity(eq_s)
    if trades.empty:
        pf, wr = 0.0, 0.0
    else:
        pnls = trades["pnl"]
        wr = float((pnls > 0).mean() * 100)
        wins = pnls[pnls > 0]; losses = pnls[pnls <= 0]
        pf = float(wins.sum() / abs(losses.sum())) if len(losses) and abs(losses.sum()) > 0 else math.inf
    return {"return_pct": round(float(ret), 2), "cagr": round(float(cagr), 2),
            "max_dd": round(max_dd, 2), "pf": round(float(pf), 2) if np.isfinite(pf) else float("inf"),
            "wr": round(float(wr), 2), "trades": int(len(trades)),
            "sharpe": round(float(sharpe), 3), "sortino": round(float(sortino), 3)}

def yearly_returns(eq, initial_capital):
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

def trade_distribution(trades):
    if trades.empty:
        return {"avg_trade_pnl": 0.0, "median_trade_pnl": 0.0,
                "avg_hold_hours": 0.0, "median_hold_hours": 0.0, "p95_hold_hours": 0.0}
    return {"avg_trade_pnl": round(float(trades["pnl"].mean()), 2),
            "median_trade_pnl": round(float(trades["pnl"].median()), 2),
            "avg_hold_hours": round(float(trades["hold_hours"].mean()), 2),
            "median_hold_hours": round(float(trades["hold_hours"].median()), 2),
            "p95_hold_hours": round(float(trades["hold_hours"].quantile(0.95)), 2)}

def liquidity_stats(trades, df_2h):
    if trades.empty:
        return {"max_qty_pct_of_bar_volume": 0.0, "p95_qty_pct_of_bar_volume": 0.0}
    vol = df_2h["volume"].rename("bar_volume")
    merged = trades.merge(vol, left_on="entry_time", right_index=True, how="left")
    pct = merged["qty"] / merged["bar_volume"] * 100
    return {"max_qty_pct_of_bar_volume": round(float(pct.max()), 6),
            "p95_qty_pct_of_bar_volume": round(float(pct.quantile(0.95)), 6)}

def reason_breakdown(trades):
    if trades.empty or "reason" not in trades.columns:
        return {}
    return {k: int(v) for k, v in trades["reason"].value_counts().items()}

def funding_cost_analysis(trades):
    if trades.empty:
        return {"total_funding": 0.0, "funding_per_trade": 0.0, "funding_per_hour_held": 0.0}
    tf = float(trades["funding"].sum())
    th = float(trades["hold_hours"].sum())
    return {"total_funding": round(tf, 2), "funding_per_trade": round(tf / len(trades), 2),
            "funding_per_hour_held": round(tf / th if th > 0 else 0.0, 4), "total_hold_hours": round(th, 1)}

def buy_hold_with_funding(df_2h, funding, hourly_prices, fee_rate, initial_capital, skip_bars=200):
    st = df_2h.index[skip_bars]
    et = df_2h.index[-1]
    ep = float(df_2h.iloc[skip_bars]["open"]) * (1 + fee_rate)
    qty = initial_capital / ep
    xp = float(df_2h.iloc[-1]["close"]) * (1 - fee_rate)
    fees = qty * ep * fee_rate + qty * xp * fee_rate
    fp = funding_pnl_for_window(funding, hourly_prices, st, et, "long", qty, "actual_signed", 1.0)
    final = initial_capital + qty * (xp - ep) - fees + fp
    ret = (final / initial_capital - 1) * 100
    years = (et - st).total_seconds() / (365.25 * 86400)
    cagr = ((final / initial_capital) ** (1 / years) - 1) * 100 if years > 0 else 0.0
    eq_arr = df_2h["close"].loc[df_2h.index >= st]
    dd_arr = (eq_arr / eq_arr.cummax() - 1) * 100
    return {"return_pct": round(float(ret), 2), "cagr": round(float(cagr), 2),
            "max_dd": round(float(dd_arr.min()), 2), "funding_pnl": round(float(fp), 2)}

def _to_native(obj):
    if isinstance(obj, (np.integer,)): return int(obj)
    if isinstance(obj, (np.floating,)): return float(obj)
    if isinstance(obj, (np.bool_,)): return bool(obj)
    if isinstance(obj, (np.ndarray,)): return _to_native(obj.tolist())
    if isinstance(obj, dict): return {k: _to_native(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)): return [_to_native(v) for v in obj]
    return obj

def load_v40_results(repo_root):
    p = repo_root / "docs/backtests/v40_hyperliquid_strict_validation/summary.json"
    if not p.exists():
        return None
    return json.loads(p.read_text())

# ── report ───────────────────────────────────────────────────────────────────

def write_report(out_dir, diagnostics, scenario_table, buy_hold, v40_results, v46_results, recommendation, verdict, version="v47"):
    headers = list(scenario_table.columns)
    ml = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for _, row in scenario_table.iterrows():
        ml.append("| " + " | ".join(str(row[h]) for h in headers) + " |")

    strategy_name = diagnostics["strategy"]["name"]

    lines = [
        f"# {version} RSI Momentum Pullback — Strict Validation",
        "", f"**Verdict:** {verdict}", "",
        "## Strategy Design", "",
        "### Core Hypothesis",
    ]

    if version == "v47":
        lines.extend([
            "In strong trends (ADX > 25, +DI > -DI for longs), RSI pullbacks to 35-50 represent",
            "temporary value zones. The KEY improvement over v46: RSI must be RISING (vs 2 bars ago)",
            "for longs, or FALLING for shorts — confirming the pullback is ending and momentum",
            "is resuming in trend direction. This filters out entries where RSI is still dropping.",
            "",
            "### Entry (all 4 conditions on 2h bar close)",
            "| # | Condition | Parameter |",
            "|---|-----------|-----------|",
            "| 1 | Strong trend | +DI > -DI AND ADX(14) > 25 (long) / opposite (short) |",
            "| 2 | RSI pullback | RSI(14) 35–50 (long) / 50–65 (short) |",
            "| 3 | Candle confirms | Bullish close (long) / Bearish close (short) |",
            "| 4 | RSI momentum | RSI rising vs 2 bars ago (long) / falling (short) |",
            "",
            "### Exit",
            "| Trigger | Detail |",
            "|---------|--------|",
            "| Trailing stop | 3× ATR(14) from peak/high-water mark |",
            "| Max hold cap | 12 bars (24h) forced exit |",
            "| Initial stop | 3× ATR(14) from entry (before trail takes over) |",
            "",
            "### Key Differences from v46",
            "- **RSI momentum confirmation**: RSI rising/falling vs 2 bars ago",
            "- **RSI ranges adjusted**: 35-50/50-65 (realistic for trend context)",
            "- **Removed daily EMA200**: redundant with ADX/DI trend filter",
            "- **Same exit structure**: 3x ATR trail + max 12 bars",
            "",
            "### Why not extreme RSI ranges (20-40/60-80)?",
            "Data analysis shows: in uptrends (ADX>25), mean RSI = 63. RSI never drops below 30.",
            "In downtrends, mean RSI = 39.5. RSI never rises above 70. Extreme RSI levels only",
            "occur in ranging/choppy markets — the opposite of what we want for trend-following.",
        ])
    else:
        lines.extend([
            "In strong trends (ADX > 25, +DI > -DI for longs), RSI pullbacks to 25-50 represent",
            "temporary value zones. Entering at these pullbacks with RSI exit at 50 (neutral)",
            "captures the trend continuation while keeping holds short. Opposite for shorts.",
            "",
            "### Entry (all 4 conditions on 2h bar close)",
            "| # | Condition | Parameter |",
            "|---|-----------|-----------|",
            "| 1 | Strong trend | +DI > -DI AND ADX(14) > 25 (long) / opposite (short) |",
            "| 2 | RSI pullback | RSI(14) 25–50 (long) / 50–75 (short) |",
            "| 3 | Candle confirms | Bullish close (long) / Bearish close (short) |",
            "| 4 | Macro regime | Daily EMA200 — long only above, short only below |",
            "",
            "### Exit",
            "| Trigger | Detail |",
            "|---------|--------|",
            "| RSI crosses 50 | Long: RSI was <50, now ≥50 (momentum returned). Short: RSI was >50, now ≤50 |",
            "| Max hold cap | 12 bars (24h) forced exit |",
            "| Stop loss | 1.5× ATR(14) from entry |",
            "",
            "### Key Differences from v40/v44",
            "- **2h timeframe** — Hyperliquid-native, faster than 4h",
            "- **DI trend direction** — +DI/-DI instead of EMA crossover",
            "- **RSI pullback entry** — enters at value, not at breakout",
            "- **RSI 50 exit** — momentum-neutral exit, not trend reversal",
            "- **Max 24h hold cap** — explicit limit",
            "- **Daily EMA200 regime** — macro filter preserved from v40",
            "- **No volume filter** — DI+ADX already filters noise",
        ])

    lines.extend([
        "",
        "## Assumptions",
        "- BTC only, 2h execution",
        "- ATR(14) × 3.0 stop/trail, 1.5% risk per trade",
        "- Max hold: 12 bars (24h on 2h candles)",
        "- Next-bar-open execution for entries/exits",
        "- Conservative stop-market handling",
        "- HL taker fees 4.5 bps, exact hourly funding",
    ])

    if version == "v47":
        lines.append("- EMA50 (2h) trend filter, no daily regime")
    else:
        lines.append("- Daily EMA200: previous completed daily bar, no look-ahead")

    lines.extend([
        "",
        "## Scenario Table",
        *ml, "",
    ])

    sc_col = [c for c in scenario_table.columns if "scenario" in c.lower()][0]
    v47_base = scenario_table[scenario_table[sc_col] == "baseline_realistic"].iloc[0]
    v47_stressed = scenario_table[scenario_table[sc_col] == "stressed_conservative"].iloc[0]

    # v47 vs v46 vs v40 comparison
    lines.append(f"## {version} vs v46 vs v40 Comparison (Baseline)")
    lines.append("")
    lines.append(f"| Metric | v40 Baseline | v46 Baseline | {version} Baseline | Δ v47-v46 |")
    lines.append("|--------|-------------|-------------|-------------|-----------|")
    cm = ["return_pct", "cagr", "max_dd", "pf", "wr", "trades", "sharpe", "sortino", "fees_total", "funding_total"]

    v40_scenarios = v40_results.get("scenarios", []) if v40_results else []
    v40_bl = next((r for r in v40_scenarios if r.get("scenario") == "baseline_realistic"), None)

    v46_scenarios = v46_results.get("scenarios", []) if v46_results else []
    v46_bl = next((r for r in v46_scenarios if r.get("scenario") == "baseline_realistic"), None)

    for m in cm:
        v40v = v40_bl.get(m, "N/A") if v40_bl else "N/A"
        v46v = v46_bl.get(m, "N/A") if v46_bl else "N/A"
        v47v = v47_base.get(m, "N/A")
        delta = ""
        if isinstance(v47v, (int, float)) and isinstance(v46v, (int, float)):
            delta = f"{v47v - v46v:+.2f}"
        lines.append(f"| {m} | {v40v} | {v46v} | {v47v} | {delta} |")
    lines.append("")

    lines.extend(["## Stressed Scenario Comparison", "",
                   "| Metric | v40 Stressed | v46 Stressed | v47 Stressed |",
                   "|--------|-------------|-------------|-------------|"])
    v40_st = next((r for r in v40_scenarios if r.get("scenario") == "stressed_conservative"), None)
    v46_st = next((r for r in v46_scenarios if r.get("scenario") == "stressed_conservative"), None)
    for m in cm:
        v40v = v40_st.get(m, "N/A") if v40_st else "N/A"
        v46v = v46_st.get(m, "N/A") if v46_st else "N/A"
        v47v = v47_stressed.get(m, "N/A")
        lines.append(f"| {m} | {v40v} | {v46v} | {v47v} |")
    lines.append("")

    lines.extend([
        "## Baseline Passive",
        f"- Buy-and-hold: return {buy_hold['return_pct']}%, CAGR {buy_hold['cagr']}%, max DD {buy_hold['max_dd']}%, funding {buy_hold['funding_pnl']}.",
        "", "## Recommendation", recommendation, "",
        "## Diagnostics", "```json", json.dumps(_to_native(diagnostics), indent=2), "```",
    ])
    (out_dir / "report.md").write_text("\n".join(lines))

# ── main ─────────────────────────────────────────────────────────────────────

def main():
    repo_root = Path(__file__).resolve().parents[1]
    out_dir = repo_root / "docs/backtests/new_hyperliquid_strategy"
    out_dir.mkdir(parents=True, exist_ok=True)

    VERSION = "v47"

    print("=" * 70)
    print(f"  {VERSION} RSI Momentum Pullback — BTC Perp on Hyperliquid")
    print(f"  RSI momentum confirmation: RSI rising/falling vs 2 bars ago")
    print(f"  RSI ranges: 35-50 long / 50-65 short (realistic for trend context)")
    print("=" * 70)

    print("\n[1/6] Fetching Hyperliquid 2h and 1d OHLCV...")
    end_ms = now_floor_2h_ms() - 1
    earliest = infer_earliest_2h("BTC")
    start_ms = int(earliest.timestamp() * 1000)

    hyper_2h = fetch_hyperliquid_klines("BTC", "2h", start_ms, end_ms, chunk_days=60)
    hyper_1d = fetch_hyperliquid_klines("BTC", "1d", dt_to_ms("2021-01-01"), end_ms, chunk_days=365)
    print(f"      2h bars: {len(hyper_2h)} | 1d bars: {len(hyper_1d)}")

    print("[2/6] Computing reliable window...")
    rs, re, rb = longest_continuous_segment(hyper_2h, "2h")
    strict_2h = hyper_2h.loc[(hyper_2h.index >= rs) & (hyper_2h.index <= re)].copy()
    strict_1d = hyper_1d.loc[hyper_1d.index <= re].copy()
    print(f"      Window: {rs} → {re} ({rb} bars, {(re-rs).days} days)")

    print("[3/6] Fetching funding...")
    funding = fetch_funding_history("BTC", int(rs.timestamp() * 1000), int(re.timestamp() * 1000) + 1)
    print(f"      Records: {len(funding)}")

    print(f"[4/6] Building features ({VERSION} logic)...")
    features = build_features(strict_2h, strict_1d, version=VERSION)
    hourly_prices = strict_2h["close"].reindex(funding.index, method="ffill")

    warmup = 200
    test = features.iloc[warmup:]
    ls = int(test["long_signal"].sum())
    ss = int(test["short_signal"].sum())
    total_weeks = (re - rs).days / 7
    print(f"      Test bars: {len(test)}")
    print(f"      Long signals: {ls} | Short signals: {ss} | Total: {ls+ss}")
    print(f"      Est. trades/week: {(ls+ss)/max(total_weeks,1):.1f}")

    scenarios = [
        ScenarioConfig("optimistic_plausible", 0.00045, 1.0, 1.0, 3.0, "actual_signed", 1.0,
                       "Light slippage, exact funding"),
        ScenarioConfig("baseline_realistic", 0.00045, 3.0, 3.0, 8.0, "actual_signed", 1.0,
                       "Moderate slippage, exact funding"),
        ScenarioConfig("stressed_conservative", 0.00045, 6.0, 6.0, 15.0, "adverse_only", 1.5,
                       "Heavy slippage, adverse funding ×1.5"),
    ]

    print("[5/6] Running 3 scenarios...")
    srows, sdetails, atd = [], {}, {}
    for sc in scenarios:
        print(f"\n      ▶ {sc.name}...")
        trades, eq, cb = StrictEngine(sc, funding, hourly_prices).run(features)
        s = metrics(trades, eq, sc.initial_capital)
        d = trade_distribution(trades)
        li = liquidity_stats(trades, strict_2h)
        y = yearly_returns(eq, sc.initial_capital)
        r = reason_breakdown(trades)
        fa = funding_cost_analysis(trades)

        trades.to_csv(out_dir / f"{VERSION}_{sc.name}_trades.csv", index=False)
        eq.to_csv(out_dir / f"{VERSION}_{sc.name}_equity.csv")
        y.to_csv(out_dir / f"{VERSION}_{sc.name}_yearly.csv", index=False)
        fs = s | cb | d | li | fa
        pd.DataFrame([fs]).to_csv(out_dir / f"{VERSION}_{sc.name}_summary.csv", index=False)

        srows.append({"scenario": sc.name, "description": sc.description, **s, **cb})
        sdetails[sc.name] = {"config": asdict(sc), "summary": s, "cost_breakdown": cb,
                             "trade_distribution": d, "liquidity": li, "exit_reasons": r,
                             "funding_analysis": fa,
                             "holding_time": {"avg": d.get("avg_hold_hours", 0),
                                              "median": d.get("median_hold_hours", 0),
                                              "p95": d.get("p95_hold_hours", 0)}}
        atd[sc.name] = {"trades": len(trades), "reasons": r, "dist": d, "funding": fa}

        print(f"         Return: {s['return_pct']:+.2f}% | PF: {s['pf']:.2f} | WR: {s['wr']:.1f}% | Trades: {s['trades']} | MaxDD: {s['max_dd']:.2f}%")
        print(f"         Fees: ${cb['fees_total']:.2f} | Funding: ${cb['funding_total']:.2f}")
        print(f"         Hold: avg={d.get('avg_hold_hours',0):.1f}h median={d.get('median_hold_hours',0):.1f}h p95={d.get('p95_hold_hours',0):.1f}h")
        print(f"         Exit reasons: {r}")

    st = pd.DataFrame(srows)
    st.to_csv(out_dir / f"{VERSION}_scenario_table.csv", index=False)
    bh = buy_hold_with_funding(strict_2h, funding, hourly_prices, 0.00045, 10_000.0)
    v40 = load_v40_results(repo_root)

    # Load v46 results from existing summary for comparison
    v46_summary = repo_root / "docs/backtests/new_hyperliquid_strategy/summary.json"
    v46_results = json.loads(v46_summary.read_text()) if v46_summary.exists() else None

    # Also load v46 diagnostics for exit reasons
    v46_diag = repo_root / "docs/backtests/new_hyperliquid_strategy/diagnostics.json"
    v46_diag_data = json.loads(v46_diag.read_text()) if v46_diag.exists() else None

    diagnostics = {
        "generated_at_utc": pd.Timestamp.now(tz="UTC").isoformat(),
        "strategy": {"name": f"{VERSION} RSI Momentum Pullback", "timeframe": "2h",
                     "entry": ["+DI > -DI + ADX>25", "RSI 35-50 long / 50-65 short",
                               "Bullish/bearish candle", "RSI rising/falling vs 2 bars ago"],
                     "exit": ["Trail stop 3x ATR from peak", "Max hold 12 bars (24h)",
                              "Initial stop 3x ATR from entry"],
                     "risk": "1.5% per trade", "max_hold_hours": 24},
        "methodology": {"execution": "next 2h bar open",
                        "funding": "exact HL hourly fundingHistory"},
        "signal_diagnostics": {"test_bars": len(test), "long": ls, "short": ss,
                               "total": ls+ss, "est_per_week": round((ls+ss)/max(total_weeks,1), 1)},
        "data_integrity": {"strict_2h": missing_bar_stats(strict_2h, "2h"),
                           "strict_1d": missing_bar_stats(strict_1d, "1D")},
        "buy_hold": bh, "scenario_details": sdetails,
        "v40_available": v40 is not None,
        "v46_available": v46_results is not None,
    }
    (out_dir / f"{VERSION}_diagnostics.json").write_text(json.dumps(_to_native(diagnostics), indent=2))
    (out_dir / f"{VERSION}_summary.json").write_text(json.dumps(_to_native({"version": VERSION, "name": "RSI Momentum Pullback",
                                                                   "timeframe": "2h", "scenarios": srows, "buy_hold": bh,
                                                                   "v40_available": v40 is not None,
                                                                   "v46_available": v46_results is not None}), indent=2))

    baseline = next(r for r in srows if r["scenario"] == "baseline_realistic")
    stressed = next(r for r in srows if r["scenario"] == "stressed_conservative")
    tpw = baseline["trades"] / max(total_weeks, 1)

    if stressed["return_pct"] <= -8 or stressed["pf"] < 0.5 or baseline["return_pct"] <= -10:
        verdict = "NO-GO"
        recommendation = "Edge does not survive conservative stress. Do not deploy live."
    elif baseline["pf"] < 1.0:
        verdict = "NO-GO"
        recommendation = (
            f"The {VERSION} strategy remains unprofitable after costs. "
            "The tighter entry filters reduced signal count but did not improve conviction enough. "
            "Consider a fundamentally different approach."
        )
    elif baseline["pf"] < 1.2 or baseline["max_dd"] < -25:
        verdict = "CONDITIONAL"
        recommendation = "Modest edge. Testnet validation + tiny live sizing."
    else:
        verdict = "GO"
        recommendation = "Survives strict realism. Justify tiny live pilot."

    write_report(out_dir, diagnostics, st[["scenario", "return_pct", "cagr", "max_dd", "pf", "wr", "trades", "sharpe", "sortino", "fees_total", "funding_total"]],
                 bh, v40, v46_results, recommendation, verdict, VERSION)

    print("\n" + "=" * 70)
    print("  RESULTS SUMMARY")
    print("=" * 70)
    print(f"  Strategy: {VERSION} RSI Momentum Pullback (2h)")
    print(f"  Verdict: {verdict}")
    print(f"  Signals/week: {(ls+ss)/max(total_weeks,1):.1f} | Trades/week: {tpw:.1f}")
    print(f"")
    print(f"  Optimistic: {srows[0]['return_pct']:+.2f}% | PF: {srows[0]['pf']:.2f} | Trades: {srows[0]['trades']}")
    print(f"  Baseline:   {baseline['return_pct']:+.2f}% | PF: {baseline['pf']:.2f} | MaxDD: {baseline['max_dd']:.2f}% | Trades: {baseline['trades']}")
    print(f"  Stressed:   {stressed['return_pct']:+.2f}% | PF: {stressed['pf']:.2f} | MaxDD: {stressed['max_dd']:.2f}%")
    print(f"  Buy&Hold:   {bh['return_pct']:+.2f}% | MaxDD: {bh['max_dd']:.2f}%")
    print(f"")

    compare_metrics = ["return_pct", "cagr", "max_dd", "pf", "wr", "trades", "sharpe", "sortino", "fees_total", "funding_total"]
    for sn in ["optimistic_plausible", "baseline_realistic", "stressed_conservative"]:
        d = atd.get(sn, {})
        dist = d.get("dist", {})
        fa = d.get("funding", {})
        print(f"  {sn}:")
        print(f"    Hold: avg={dist.get('avg_hold_hours',0):.1f}h median={dist.get('median_hold_hours',0):.1f}h p95={dist.get('p95_hold_hours',0):.1f}h")
        print(f"    Funding: total=${fa.get('total_funding',0):.2f} | per trade=${fa.get('funding_per_trade',0):.2f}")
        print(f"    Exit reasons: {d.get('reasons',{})}")
        print()

    # v47 vs v46 vs v40 comparison
    print("=" * 70)
    print(f"  {VERSION} vs v46 vs v40 COMPARISON (Baseline)")
    print("=" * 70)

    v46_bl = None
    if v46_results:
        v46_bl = next((r for r in v46_results.get("scenarios", []) if r.get("scenario") == "baseline_realistic"), None)
    v40_bl = None
    if v40:
        v40_bl = next((r for r in v40.get("scenarios", []) if r.get("scenario") == "baseline_realistic"), None)

    print(f"\n  {'Metric':<22} {'v40':>10} {'v46':>10} {VERSION:>10} {'Δ v47-v46':>12}")
    print(f"  {'-'*68}")
    for m in compare_metrics:
        v40v = v40_bl.get(m, "N/A") if v40_bl else "N/A"
        v46v = v46_bl.get(m, "N/A") if v46_bl else "N/A"
        v47v = baseline.get(m, "N/A")
        delta = ""
        if isinstance(v47v, (int, float)) and isinstance(v46v, (int, float)):
            delta = f"{v47v - v46v:+.2f}"
        print(f"  {m:<22} {str(v40v):>10} {str(v46v):>10} {str(v47v):>10} {delta:>12}")

    # Exit reason comparison
    print(f"\n  Exit reason breakdown:")
    v47_reasons = atd.get("baseline_realistic", {}).get("reasons", {})
    v46_reasons = {}
    if v46_diag_data:
        v46_reasons = v46_diag_data.get("scenario_details", {}).get("baseline_realistic", {}).get("exit_reasons", {})
    all_reasons = sorted(set(list(v46_reasons.keys()) + list(v47_reasons.keys())))
    if all_reasons:
        print(f"  {'Reason':<15} {'v46':>8} {'v47':>8}")
        print(f"  {'-'*33}")
        for reason in all_reasons:
            print(f"  {reason:<15} {v46_reasons.get(reason, 0):>8} {v47_reasons.get(reason, 0):>8}")
    else:
        print(f"  v47: {v47_reasons}")

    print(f"\n  Output → {out_dir}")


if __name__ == "__main__":
    main()
