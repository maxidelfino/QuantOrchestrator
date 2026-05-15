#!/usr/bin/env python3
"""v50 Entry Optimization — push CAGR from 19.5% to 23%+

Goal: v48b (exit-optimized) achieved 19.5% CAGR / PF 2.31 / MaxDD -3.5% / 30 trades.
Target: CAGR >= 23%, PF > 1.5, MaxDD > -10%.

Tests entry-side improvements ONLY (exits stay at v48b: trail 3x ATR, max hold 16 bars):

A. Multi-timeframe trend filter: 1h longs only when 4h close > 4h EMA(20),
   shorts only when 4h close < 4h EMA(20)
B. RSI divergence detection: price lower-low + RSI higher-low (bullish),
   price higher-high + RSI lower-high (bearish) — replaces simple RSI momentum
C. Volume spike confirmation: current bar volume > 1.3x avg volume(20)
D. Support/resistance proximity: long within 1% of swing low(20),
   short within 1% of swing high(20)
E. MACD histogram flip: histogram crosses zero (neg→pos for longs, pos→neg for shorts)
F. Position sizing by conviction: risk 1.5% when ADX>30, 1% when ADX 20-30

Then combines the best 2-3.
"""

from __future__ import annotations

import json, math, time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

HYPERLIQUID_INFO = "https://api.hyperliquid.xyz/info"
_session = requests.Session()
_adapter = requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=8, max_retries=2)
_session.mount("https://", _adapter)


# ── helpers ──────────────────────────────────────────────────────────────────

def dt_to_ms(s: str) -> int:
    return int(datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)

def now_floor_ms(interval: str) -> int:
    now = pd.Timestamp.now(tz="UTC")
    if interval == "1h":
        floored = now.floor("h")
    elif interval == "2h":
        floored_hour = (now.hour // 2) * 2
        floored = now.floor("D") + pd.Timedelta(hours=floored_hour)
    else:
        floored = now.floor(interval)
    return int(floored.timestamp() * 1000)

def bars_per_year_for(interval: str) -> int:
    return {"1h": 8760, "2h": 4380, "4h": 2190, "1d": 365}.get(interval, 4380)

def freq_for(interval: str) -> str:
    return {"1h": "1h", "2h": "2h", "4h": "4h", "1d": "1D"}.get(interval, interval)

def fetch_json(payload, timeout=45):
    for attempt in range(3):
        try:
            r = _session.post(HYPERLIQUID_INFO, json=payload, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError) as e:
            if attempt == 2:
                raise
            time.sleep(1 * (2 ** attempt))

def fetch_klines(coin, interval, start_ms, end_ms, chunk_days=90):
    out = []
    cur = start_ms
    chunk_ms = chunk_days * 24 * 60 * 60 * 1000
    while cur < end_ms:
        chunk_end = min(cur + chunk_ms, end_ms)
        data = fetch_json({"type": "candleSnapshot", "req": {"coin": coin, "interval": interval, "startTime": cur, "endTime": chunk_end}})
        out.extend(data)
        cur = chunk_end
        time.sleep(0.05)
    if not out:
        raise RuntimeError(f"No data for {coin} {interval}")
    df = pd.DataFrame(out)
    df["t"] = pd.to_datetime(df["t"], unit="ms", utc=True)
    for c in ["o", "h", "l", "c", "v"]:
        df[c] = df[c].astype(float)
    df = df.rename(columns={"t": "time", "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
    return df.set_index("time")[["open", "high", "low", "close", "volume"]].sort_index().pipe(
        lambda d: d[~d.index.duplicated(keep="last")])

def fetch_funding(coin, start_ms, end_ms, chunk_days=120):
    out = []
    cur = start_ms
    chunk_ms = chunk_days * 24 * 60 * 60 * 1000
    while cur < end_ms:
        chunk_end = min(cur + chunk_ms, end_ms)
        data = fetch_json({"type": "fundingHistory", "coin": coin, "startTime": cur, "endTime": chunk_end}, timeout=60)
        out.extend(data)
        cur = chunk_end + 1
        time.sleep(0.05)
    if not out:
        return pd.DataFrame(columns=["fundingRate"])
    df = pd.DataFrame(out)
    df["time"] = pd.to_datetime(df["time"], unit="ms", utc=True)
    df["fundingRate"] = df["fundingRate"].astype(float)
    return df.set_index("time")[["fundingRate"]].sort_index().pipe(
        lambda d: d[~d.index.duplicated(keep="last")])

def infer_earliest(coin, interval):
    probes = [("2025-03-01", "2025-03-31"), ("2025-06-01", "2025-06-30"),
              ("2025-09-01", "2025-09-30"), ("2025-10-01", "2025-10-31"),
              ("2025-11-01", "2025-11-30")]
    first = None
    for s, e in probes:
        try:
            data = fetch_json({"type": "candleSnapshot", "req": {"coin": coin, "interval": interval, "startTime": dt_to_ms(s), "endTime": dt_to_ms(e)}})
            if data:
                ts = pd.to_datetime(data[0]["t"], unit="ms", utc=True)
                first = ts if first is None else min(first, ts)
        except Exception:
            continue
    return first

def longest_continuous_segment(df, freq):
    step = pd.Timedelta(freq)
    segments, start, prev, count = [], df.index[0], df.index[0], 1
    for ts in df.index[1:]:
        if ts - prev == step:
            count += 1
        else:
            segments.append((start, prev, count))
            start, count = ts, 1
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
    up, down = df["high"] - df["high"].shift(1), df["low"].shift(1) - df["low"]
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    plus_di = 100 * (plus_dm.ewm(alpha=1.0 / period, adjust=False).mean() / atr_s.replace(0, np.nan))
    minus_di = 100 * (minus_dm.ewm(alpha=1.0 / period, adjust=False).mean() / atr_s.replace(0, np.nan))
    dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1.0 / period, adjust=False).mean(), plus_di, minus_di

def compute_rsi(df, period=14):
    delta = df["close"].diff()
    g, l = delta.clip(lower=0), (-delta).clip(lower=0)
    rs = g.ewm(alpha=1.0 / period, adjust=False).mean() / l.ewm(alpha=1.0 / period, adjust=False).mean().replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def compute_macd(df, fast=12, slow=26, signal=9):
    ema_fast = df["close"].ewm(span=fast, adjust=False).mean()
    ema_slow = df["close"].ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


# ── v48b baseline feature builder (entry: v48a, exit: max_hold=16) ──────────

def build_features_v48b(df):
    """Baseline: v48a entry logic — ADX>20, RSI pullback, candle + DI confirm.
    Exit uses max_hold=16 bars (v48b winner).
    """
    out = df.copy()
    out["atr14"] = compute_atr(out, 14)
    out["adx14"], out["plus_di"], out["minus_di"] = compute_adx_di(out, 14)
    out["rsi14"] = compute_rsi(out, 14)
    out["rsi_rising"] = out["rsi14"] > out["rsi14"].shift(2)
    out["rsi_falling"] = out["rsi14"] < out["rsi14"].shift(2)
    out["rsi_pullback"] = (out["rsi14"] >= 35) & (out["rsi14"] <= 50)
    out["rsi_overextended"] = (out["rsi14"] >= 50) & (out["rsi14"] <= 65)
    out["bullish"] = out["close"] > out["open"]
    out["bearish"] = out["close"] < out["open"]
    out["adx_ok"] = out["adx14"] > 20
    out["uptrend_adx"] = (out["plus_di"] > out["minus_di"]) & out["adx_ok"]
    out["downtrend_adx"] = (out["minus_di"] > out["plus_di"]) & out["adx_ok"]
    out["long_signal"] = out["uptrend_adx"] & out["rsi_pullback"] & out["bullish"] & out["rsi_rising"]
    out["short_signal"] = out["downtrend_adx"] & out["rsi_overextended"] & out["bearish"] & out["rsi_falling"]
    return out


# ── v50 entry variant feature builders ───────────────────────────────────────

def _find_swing_lows(df, lookback=20):
    """Find swing lows: bar where low is lowest in lookback bars on each side."""
    lows = df["low"].rolling(lookback, min_periods=lookback//2, center=True).min()
    return lows

def _find_swing_highs(df, lookback=20):
    """Find swing highs: bar where high is highest in lookback bars on each side."""
    highs = df["high"].rolling(lookback, min_periods=lookback//2, center=True).max()
    return highs

def _detect_rsi_divergence(df, lookback=10):
    """Detect RSI divergence over a lookback window.

    Bullish divergence: price makes lower low but RSI makes higher low.
    Bearish divergence: price makes higher high but RSI makes lower high.

    Returns (bullish_div, bearish_div) boolean series.
    """
    n = len(df)
    bullish_div = pd.Series(False, index=df.index)
    bearish_div = pd.Series(False, index=df.index)

    for i in range(lookback, n):
        window_prices = df["low"].iloc[i-lookback:i+1]
        window_rsi = df["rsi14"].iloc[i-lookback:i+1]

        # Find two most recent local lows in price
        price_lows = []
        for j in range(2, len(window_prices) - 1):
            if window_prices.iloc[j] < window_prices.iloc[j-1] and window_prices.iloc[j] < window_prices.iloc[j+1]:
                price_lows.append((j, window_prices.iloc[j], window_rsi.iloc[j]))

        if len(price_lows) >= 2:
            # Compare two most recent lows
            l1, l2 = price_lows[-2], price_lows[-1]
            # Bullish div: price lower low, RSI higher low
            if l2[1] < l1[1] and l2[2] > l1[2]:
                bullish_div.iloc[i] = True

        # Find two most recent local highs in price
        price_highs_p = df["high"].iloc[i-lookback:i+1]
        window_rsi_h = df["rsi14"].iloc[i-lookback:i+1]
        price_highs = []
        for j in range(2, len(price_highs_p) - 1):
            if price_highs_p.iloc[j] > price_highs_p.iloc[j-1] and price_highs_p.iloc[j] > price_highs_p.iloc[j+1]:
                price_highs.append((j, price_highs_p.iloc[j], window_rsi_h.iloc[j]))

        if len(price_highs) >= 2:
            h1, h2 = price_highs[-2], price_highs[-1]
            # Bearish div: price higher high, RSI lower high
            if h2[1] > h1[1] and h2[2] < h1[2]:
                bearish_div.iloc[i] = True

    return bullish_div, bearish_div


def _apply_variant_A(df, h4_df):
    """A: Multi-timeframe trend filter — 4h close vs 4h EMA(20)."""
    out = df.copy()
    h4_ema20 = h4_df["close"].ewm(span=20, adjust=False).mean()
    # Forward-fill 4h EMA to 1h bars, shift 1 bar to avoid look-ahead
    ema_aligned = h4_ema20.reindex(out.index, method="ffill").shift(1)
    close_aligned = h4_df["close"].reindex(out.index, method="ffill").shift(1)

    out["mtf_trend_long"] = close_aligned > ema_aligned
    out["mtf_trend_short"] = close_aligned < ema_aligned
    out["long_signal"] = out["long_signal"] & out["mtf_trend_long"]
    out["short_signal"] = out["short_signal"] & out["mtf_trend_short"]
    return out


def _apply_variant_B(df):
    """B: RSI divergence detection — replaces simple RSI momentum."""
    out = df.copy()
    bullish_div, bearish_div = _detect_rsi_divergence(out, lookback=10)
    out["bullish_div"] = bullish_div
    out["bearish_div"] = bearish_div

    # Replace rsi_rising/rsi_falling with divergence signals
    # Keep original ADX + RSI range + candle filters, but swap momentum for divergence
    out["long_signal"] = (out["uptrend_adx"] & out["rsi_pullback"] & out["bullish"] & out["bullish_div"])
    out["short_signal"] = (out["downtrend_adx"] & out["rsi_overextended"] & out["bearish"] & out["bearish_div"])
    return out


def _apply_variant_C(df):
    """C: Volume spike confirmation — volume > 1.3x avg volume(20)."""
    out = df.copy()
    vol_avg = df["volume"].rolling(20, min_periods=10).mean()
    out["vol_spike"] = df["volume"] > 1.3 * vol_avg
    out["long_signal"] = out["long_signal"] & out["vol_spike"]
    out["short_signal"] = out["short_signal"] & out["vol_spike"]
    return out


def _apply_variant_D(df):
    """D: Support/resistance proximity — within 1% of swing low/high(20)."""
    out = df.copy()
    swing_low = _find_swing_lows(out, 20)
    swing_high = _find_swing_highs(out, 20)

    out["near_support"] = (df["close"] - swing_low) / swing_low <= 0.01
    out["near_resistance"] = (swing_high - df["close"]) / swing_high <= 0.01
    out["long_signal"] = out["long_signal"] & out["near_support"]
    out["short_signal"] = out["short_signal"] & out["near_resistance"]
    return out


def _apply_variant_E(df):
    """E: MACD histogram flip — histogram crosses zero."""
    out = df.copy()
    _, _, hist = compute_macd(out)
    out["macd_hist"] = hist
    out["macd_flip_long"] = (hist > 0) & (hist.shift(1) <= 0)
    out["macd_flip_short"] = (hist < 0) & (hist.shift(1) >= 0)
    out["long_signal"] = out["long_signal"] & out["macd_flip_long"]
    out["short_signal"] = out["short_signal"] & out["macd_flip_short"]
    return out


def _apply_variant_F_config(sc):
    """F: Position sizing by conviction — handled in engine, not features.
    Returns a modified ScenarioConfig."""
    # F doesn't change signals, it changes risk_pct dynamically in the engine
    return sc  # config stays same, engine handles it


def build_v50_variant(df, variant, h4_df=None):
    """Build features for any v50 variant combination.

    variant: string like "A", "B", "C", "AB", "ABC", etc.
    """
    feat = build_features_v48b(df)

    if "A" in variant:
        feat = _apply_variant_A(feat, h4_df)
    if "B" in variant:
        feat = _apply_variant_B(feat)
    if "C" in variant:
        feat = _apply_variant_C(feat)
    if "D" in variant:
        feat = _apply_variant_D(feat)
    if "E" in variant:
        feat = _apply_variant_E(feat)
    # F is handled in the engine via dynamic risk_pct

    return feat


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
    stop_atr_mult: float = 3.0
    trail_atr_mult: float = 3.0
    tp_atr_mult: float = 0.0
    max_hold_bars: int = 16  # v48b exit: 16 bars
    partial_exit_bars: int = 0
    partial_exit_pct: float = 0.5
    warmup_bars: int = 200
    conviction_sizing: bool = False  # F: dynamic risk by ADX


# ── cost model ───────────────────────────────────────────────────────────────

def apply_slippage(price, side, action, bps):
    bump = bps / 10_000.0
    if action == "entry":
        return price * (1 + bump) if side == "long" else price * (1 - bump)
    return price * (1 - bump) if side == "long" else price * (1 + bump)

def funding_pnl(funding, hourly_prices, entry_time, exit_time, side, qty, mode, mult):
    if funding.empty:
        return 0.0
    w = funding.loc[(funding.index > entry_time) & (funding.index <= exit_time)]
    if w.empty:
        return 0.0
    prices = hourly_prices.reindex(w.index, method="ffill")
    sign = 1.0 if side == "long" else -1.0
    signed = -sign * w["fundingRate"] * qty * prices
    if mode == "actual_signed":
        return float((signed * mult).sum())
    return float(signed.clip(upper=0.0).sum() * mult)


# ── engine with conviction sizing (variant F) ────────────────────────────────

class Engine:
    def __init__(self, sc, funding, hp):
        self.sc, self.funding, self.hp = sc, funding, hp

    def _get_risk_pct(self, bar):
        """Variant F: dynamic risk by ADX conviction."""
        if self.sc.conviction_sizing:
            adx = bar.get("adx14", 0)
            if not np.isnan(adx) and adx > 30:
                return 0.015  # High conviction
            return 0.010      # Normal conviction
        return self.sc.risk_pct

    def run(self, df, coin=""):
        sc = self.sc
        pos, qty, entry, et, st = 0, 0.0, 0.0, None, None
        stop, trail, hw = np.nan, np.nan, np.nan
        bars, realized, trades, eq_rows = 0, 0.0, [], []
        ftot, ffees = 0.0, 0.0

        for i in range(sc.warmup_bars, len(df) - 1):
            t, b, nb, nt = df.index[i], df.iloc[i], df.iloc[i+1], df.index[i+1]
            if any(np.isnan(b.get(c, np.nan)) for c in ["atr14","adx14","rsi14"]):
                continue
            exited = False
            atr = float(b["atr14"]) if not np.isnan(b["atr14"]) else 0

            # Update trail
            if pos == 1 and atr > 0:
                hw = max(hw, float(b["high"]))
                trail = hw - sc.trail_atr_mult * atr
            elif pos == -1 and atr > 0:
                hw = min(hw, float(b["low"]))
                trail = hw + sc.trail_atr_mult * atr

            def do_exit(reason, exit_qty=None):
                nonlocal pos, qty, entry, et, st, stop, trail, hw, bars, realized, ftot, ffees
                side = "long" if pos == 1 else "short"
                eqty = exit_qty if exit_qty is not None else qty

                if reason == "trail_stop":
                    raw = min(float(trail), float(b["open"])) if pos == 1 else max(float(trail), float(b["open"]))
                    px = apply_slippage(raw, side, "exit", sc.stop_slippage_bps)
                elif reason == "stop":
                    raw = min(float(stop), float(b["open"])) if pos == 1 else max(float(stop), float(b["open"]))
                    px = apply_slippage(raw, side, "exit", sc.stop_slippage_bps)
                elif reason == "take_profit":
                    px = apply_slippage(float(b["open"]), side, "exit", sc.exit_slippage_bps)
                elif reason == "max_hold":
                    px = apply_slippage(float(nb["open"]), side, "exit", sc.exit_slippage_bps)
                else:
                    px = apply_slippage(float(b["close"]), side, "exit", sc.exit_slippage_bps)

                fp = funding_pnl(self.funding, self.hp, et, t, side, eqty, sc.funding_mode, sc.funding_multiplier)
                fees = eqty * entry * sc.fee_rate + eqty * px * sc.fee_rate
                gp = eqty * (px - entry) if pos == 1 else eqty * (entry - px)
                pnl = gp - fees + fp
                trades.append({"signal_time": st, "entry_time": et, "exit_time": t, "dir": side,
                               "entry": entry, "exit": px, "qty": eqty, "gross_pnl": gp, "fees": -fees,
                               "funding": fp, "pnl": pnl, "hold_hours": (t - et).total_seconds() / 3600,
                               "reason": reason, "coin": coin})
                realized += pnl; ftot += fp; ffees += fees

                # Full exit
                pos, qty, entry, et, st, stop, trail, hw, bars = 0, 0.0, 0.0, None, None, np.nan, np.nan, np.nan, 0

            # ── Check exits ──

            # Trail stop
            if pos == 1 and not np.isnan(trail) and b["low"] <= trail:
                do_exit("trail_stop"); exited = True
            elif pos == -1 and not np.isnan(trail) and b["high"] >= trail:
                do_exit("trail_stop"); exited = True

            # Initial stop
            if not exited and pos == 1 and not np.isnan(stop) and b["low"] <= stop:
                do_exit("stop"); exited = True
            elif not exited and pos == -1 and not np.isnan(stop) and b["high"] >= stop:
                do_exit("stop"); exited = True

            # Max hold (v48b: 16 bars)
            if not exited and pos != 0:
                bars += 1
                if bars >= sc.max_hold_bars:
                    do_exit("max_hold"); exited = True

            # ── Entry ──
            if pos == 0:
                equity = sc.initial_capital + realized
                risk = equity * self._get_risk_pct(b)
                if bool(b.get("long_signal")) and atr > 0:
                    ep = apply_slippage(float(nb["open"]), "long", "entry", sc.entry_slippage_bps)
                    q = risk / (sc.stop_atr_mult * atr)
                    if q > 0:
                        pos, qty, entry, et, st = 1, float(q), ep, nt, t
                        stop = ep - sc.stop_atr_mult * atr
                        trail = ep - sc.trail_atr_mult * atr
                        hw = float(ep); bars = 0
                elif bool(b.get("short_signal")) and atr > 0:
                    ep = apply_slippage(float(nb["open"]), "short", "entry", sc.entry_slippage_bps)
                    q = risk / (sc.stop_atr_mult * atr)
                    if q > 0:
                        pos, qty, entry, et, st = -1, float(q), ep, nt, t
                        stop = ep + sc.stop_atr_mult * atr
                        trail = ep + sc.trail_atr_mult * atr
                        hw = float(ep); bars = 0

            # Equity tracking
            op = qty * (b["close"] - entry) if pos == 1 else (qty * (entry - b["close"]) if pos == -1 else 0)
            eq_rows.append({"time": t, "equity": sc.initial_capital + realized + op})

        # Close remaining position at end
        if pos != 0 and et is not None:
            t = df.index[-1]
            side = "long" if pos == 1 else "short"
            px = apply_slippage(float(df.iloc[-1]["close"]), side, "exit", sc.exit_slippage_bps)
            fp = funding_pnl(self.funding, self.hp, et, t, side, qty, sc.funding_mode, sc.funding_multiplier)
            fees = qty * entry * sc.fee_rate + qty * px * sc.fee_rate
            gp = qty * (px - entry) if pos == 1 else qty * (entry - px)
            pnl = gp - fees + fp
            trades.append({"signal_time": st, "entry_time": et, "exit_time": t, "dir": side,
                           "entry": entry, "exit": px, "qty": qty, "gross_pnl": gp, "fees": -fees,
                           "funding": fp, "pnl": pnl, "hold_hours": (t - et).total_seconds() / 3600,
                           "reason": "eod", "coin": coin})

        td = pd.DataFrame(trades)
        ed = pd.DataFrame(eq_rows).set_index("time") if eq_rows else pd.DataFrame(columns=["equity"])
        return td, ed, {"fees_total": round(ffees, 2), "funding_total": round(ftot, 2)}


# ── metrics ──────────────────────────────────────────────────────────────────

def calc_metrics(trades, eq, capital, bpy):
    if eq.empty:
        return {k: 0.0 for k in ["return_pct","cagr","max_dd","pf","wr","trades","sharpe","sortino"]}
    s = eq["equity"].dropna()
    ret = (s.iloc[-1] / capital - 1) * 100
    years = (s.index[-1] - s.index[0]).total_seconds() / (365.25 * 86400)
    cagr = ((s.iloc[-1] / capital) ** (1 / years) - 1) * 100 if years > 0 else 0
    dd = float(((s / s.cummax() - 1) * 100).min())
    r = s.pct_change().dropna()
    sh = float(r.mean() / r.std() * np.sqrt(bpy)) if len(r) > 1 and r.std() > 0 else 0
    dn = r[r < 0]
    so = float(r.mean() / dn.std() * np.sqrt(bpy)) if len(dn) > 1 and dn.std() > 0 else 0
    if trades.empty:
        pf, wr = 0, 0
    else:
        p = trades["pnl"]
        wr = float((p > 0).mean() * 100)
        w, l = p[p > 0], p[p <= 0]
        pf = float(w.sum() / abs(l.sum())) if len(l) and abs(l.sum()) > 0 else float("inf")
    return {"return_pct": round(ret, 2), "cagr": round(cagr, 2), "max_dd": round(dd, 2),
            "pf": round(pf, 2) if np.isfinite(pf) else float("inf"), "wr": round(wr, 2),
            "trades": len(trades), "sharpe": round(sh, 3), "sortino": round(so, 3)}

def _native(o):
    if isinstance(o, (np.integer,)): return int(o)
    if isinstance(o, (np.floating,)): return float(o)
    if isinstance(o, dict): return {k: _native(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)): return [_native(v) for v in o]
    return o


# ── variant → config mapper ──────────────────────────────────────────────────

def config_for_variant(base_sc, variant):
    """Return a ScenarioConfig modified for the given variant string."""
    kwargs = {
        "name": base_sc.name,
        "fee_rate": base_sc.fee_rate,
        "entry_slippage_bps": base_sc.entry_slippage_bps,
        "exit_slippage_bps": base_sc.exit_slippage_bps,
        "stop_slippage_bps": base_sc.stop_slippage_bps,
        "funding_mode": base_sc.funding_mode,
        "funding_multiplier": base_sc.funding_multiplier,
        "description": base_sc.description,
        "initial_capital": base_sc.initial_capital,
        "risk_pct": base_sc.risk_pct,
        "stop_atr_mult": base_sc.stop_atr_mult,
        "trail_atr_mult": base_sc.trail_atr_mult,
        "tp_atr_mult": base_sc.tp_atr_mult,
        "max_hold_bars": base_sc.max_hold_bars,
        "partial_exit_bars": base_sc.partial_exit_bars,
        "partial_exit_pct": base_sc.partial_exit_pct,
        "warmup_bars": base_sc.warmup_bars,
        "conviction_sizing": False,
    }

    if "F" in variant:
        kwargs["conviction_sizing"] = True

    return ScenarioConfig(**kwargs)


# ── runner ───────────────────────────────────────────────────────────────────

def run_variant(variant_name, variant_flags, coin, interval, base_sc, funding, hp, h4_df, ohlcv_df, out_dir, label):
    """Run a single v50 variant."""
    bpy = bars_per_year_for(interval)
    freq = freq_for(interval)

    sc = config_for_variant(base_sc, variant_flags)
    sc.name = base_sc.name

    feat = build_v50_variant(ohlcv_df, variant_flags, h4_df)
    weeks = (hp.index[-1] - hp.index[0]).days / 7
    ls = int(feat.iloc[200:]["long_signal"].sum())
    ss = int(feat.iloc[200:]["short_signal"].sum())
    print(f"  [{variant_name}] Signals: {ls+ss} (L:{ls} S:{ss}) | ~{(ls+ss)/max(weeks,1):.1f}/week", end=" ")

    td, ed, costs = Engine(sc, funding, hp).run(feat, coin)
    m = calc_metrics(td, ed, sc.initial_capital, bpy)
    tpw = m["trades"] / max(weeks, 1)
    row = {"scenario": sc.name, **m, **costs, "trades_per_week": round(tpw, 2)}

    print(f"-> CAGR {m['cagr']:.1f}% PF {m['pf']:.1f} DD {m['max_dd']:.1f}% T {m['trades']}")

    if not td.empty:
        td.to_csv(out_dir / f"{label}_{variant_name}_trades.csv", index=False)
    if not ed.empty:
        ed.to_csv(out_dir / f"{label}_{variant_name}_equity.csv")

    return row


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    repo_root = Path(__file__).resolve().parents[1]
    out_dir = repo_root / "docs/backtests/v50_entry_optimization"
    out_dir.mkdir(parents=True, exist_ok=True)

    COIN = "BTC"
    INTERVAL = "1h"
    # v48b baseline: trail 3x ATR, max hold 16 bars, 1.5% risk
    base_sc = ScenarioConfig("v48b_baseline", 0.00045, 3.0, 3.0, 8.0, "actual_signed", 1.0,
                             "v48b (trail 3x, max_hold=16)")

    # ── Fetch data once ──
    print("Fetching 1h data...")
    end_ms = now_floor_ms(INTERVAL) - 1
    earliest = infer_earliest(COIN, INTERVAL)
    if not earliest:
        raise RuntimeError(f"No {INTERVAL} data for {COIN}")
    klines_1h = fetch_klines(COIN, INTERVAL, int(earliest.timestamp()*1000), end_ms)
    rs, re, _ = longest_continuous_segment(klines_1h, freq_for(INTERVAL))
    strict_1h = klines_1h.loc[(klines_1h.index >= rs) & (klines_1h.index <= re)].copy()
    print(f"1h Window: {rs.date()} -> {re.date()} ({(re-rs).days}d, {len(strict_1h)} bars)")

    funding = fetch_funding(COIN, int(rs.timestamp()*1000), int(re.timestamp()*1000))
    hp_1h = strict_1h["close"].reindex(funding.index, method="ffill")

    # Fetch 4h data for variant A (multi-timeframe trend filter)
    print("Fetching 4h data...")
    h4_start = int((rs - pd.Timedelta(days=60)).timestamp() * 1000)
    klines_4h = fetch_klines(COIN, "4h", h4_start, end_ms)
    h4_df = klines_4h[["close"]].copy()

    # ── Define variants ──
    variants = {
        "v48b": ("", "Baseline v48b (trail 3x, max_hold=16)"),
        "A": ("A", "MTF trend filter (4h EMA20)"),
        "B": ("B", "RSI divergence detection"),
        "C": ("C", "Volume spike > 1.3x avg(20)"),
        "D": ("D", "S/R proximity (1% swing)"),
        "E": ("E", "MACD histogram flip"),
        "F": ("F", "Conviction sizing (ADX>30=1.5%)"),
        # Combinations: test best 2-3
        "AB": ("AB", "MTF trend + RSI divergence"),
        "AC": ("AC", "MTF trend + volume spike"),
        "AE": ("AE", "MTF trend + MACD flip"),
        "AF": ("AF", "MTF trend + conviction sizing"),
        "BC": ("BC", "RSI div + volume spike"),
        "BE": ("BE", "RSI div + MACD flip"),
        "BF": ("BF", "RSI div + conviction sizing"),
        "CE": ("CE", "Volume spike + MACD flip"),
        "CF": ("CF", "Volume spike + conviction sizing"),
        "EF": ("EF", "MACD flip + conviction sizing"),
        # Best 3-combos (added after singles run)
        "ABC": ("ABC", "MTF + RSI div + volume"),
        "ABE": ("ABE", "MTF + RSI div + MACD"),
        "ACF": ("ACF", "MTF + volume + conviction"),
        "AEF": ("AEF", "MTF + MACD + conviction"),
    }

    results = {}
    for vname, (flags, desc) in variants.items():
        print(f"  [{vname}] {desc}...", end=" ")
        row = run_variant(vname, flags, COIN, INTERVAL, base_sc, funding, hp_1h, h4_df, strict_1h, out_dir, "v50")
        results[vname] = {"row": row, "desc": desc}

    # ── Summary table ──
    print("\n" + "=" * 120)
    print("  V50 ENTRY OPTIMIZATION — FULL COMPARISON")
    print("=" * 120)

    all_cols = ["v48b", "A", "B", "C", "D", "E", "F",
                "AB", "AC", "AE", "AF", "BC", "BE", "BF", "CE", "CF", "EF",
                "ABC", "ABE", "ACF", "AEF"]

    header = f"  {'Metric':<16}" + "".join(f" {c:>10}" for c in all_cols)
    print(header)
    print("  " + "-" * (16 + 11 * len(all_cols)))

    for m in ["cagr", "max_dd", "pf", "wr", "trades", "trades_per_week"]:
        line = f"  {m:<16}"
        for c in all_cols:
            r = results.get(c, {}).get("row", {})
            val = r.get(m, "N/A")
            line += f" {str(val):>10}"
        print(line)

    # ── Verdict ──
    print(f"\n  VERDICT (target: CAGR>=23%, MaxDD>-10%, PF>1.5):")
    print(f"  {'-'*100}")

    best = None
    best_cagr = -999
    for vname in all_cols:
        r = results.get(vname, {}).get("row", {})
        if not r:
            continue
        cagr = r.get("cagr", 0)
        dd = r.get("max_dd", 0)
        pf = r.get("pf", 0)
        ok = "PASS" if cagr >= 23 and dd > -10 and pf > 1.5 else "FAIL"
        print(f"  {vname:>5} ({results[vname]['desc']:40s}): CAGR {cagr:>6.1f}%  DD {dd:>6.1f}%  PF {pf:>5.2f}  T {r.get('trades',0):>3}  -> {ok}")
        if cagr >= 23 and dd > -10 and pf > 1.5 and cagr > best_cagr:
            best = vname
            best_cagr = cagr

    if best:
        print(f"\n  BEST: {best} - reaches 23%+ CAGR with PF>1.5 and MaxDD>-10%")
    else:
        # Find highest CAGR that meets PF and DD constraints
        constrained_best = None
        constrained_cagr = -999
        for vname in all_cols:
            r = results.get(vname, {}).get("row", {})
            if not r:
                continue
            cagr = r.get("cagr", 0)
            dd = r.get("max_dd", 0)
            pf = r.get("pf", 0)
            if dd > -10 and pf > 1.5 and cagr > constrained_cagr:
                constrained_best = vname
                constrained_cagr = cagr

        if constrained_best:
            print(f"\n  NO variant reaches 23% CAGR with PF>1.5 and MaxDD>-10%")
            print(f"  Best constrained: {constrained_best} - CAGR {constrained_cagr:.1f}% (PF {results[constrained_best]['row']['pf']:.2f}, DD {results[constrained_best]['row']['max_dd']:.1f}%)")
            print(f"  Gap to target: {23 - constrained_cagr:.1f} percentage points")
            print(f"  Bottleneck analysis:")
            # Analyze why
            r_base = results["v48b"]["row"]
            r_best = results[constrained_best]["row"]
            trade_delta = r_best["trades"] - r_base["trades"]
            wr_delta = r_best["wr"] - r_base["wr"]
            print(f"    - Trade count change: {trade_delta:+d} vs baseline ({r_base['trades']} -> {r_best['trades']})")
            print(f"    - Win rate change: {wr_delta:+.1f}pp ({r_base['wr']:.1f}% -> {r_best['wr']:.1f}%)")
            print(f"    - Entry filters are REDUCING trade frequency too much OR not improving win rate enough")
            print(f"    - Consider: looser filter thresholds, or combining with exit improvements from v49")
        else:
            abs_best = None
            abs_cagr = -999
            for vname in all_cols:
                r = results.get(vname, {}).get("row", {})
                if r and r.get("cagr", 0) > abs_cagr:
                    abs_best = vname
                    abs_cagr = r["cagr"]
            print(f"\n  NO variant reaches 23% CAGR with acceptable risk")
            print(f"  Highest CAGR (any risk): {abs_best} - {abs_cagr:.1f}%")

    # ── Save summary ──
    summary = {}
    for vname in all_cols:
        r = results.get(vname, {}).get("row", {})
        if r:
            summary[vname] = {**r, "desc": results[vname]["desc"]}
    summary["best_variant"] = best or constrained_best or "none"
    summary["target"] = {"cagr_min": 23, "pf_min": 1.5, "max_dd_max": -10}

    (out_dir / "summary.json").write_text(json.dumps(_native(summary), indent=2))
    print(f"\n  Results saved to: {out_dir}")
    print(f"  summary.json written")


if __name__ == "__main__":
    main()
