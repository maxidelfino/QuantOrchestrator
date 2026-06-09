#!/usr/bin/env python3
"""v49 Exit Optimization — push CAGR to 23%+ without destroying PF or MaxDD

Goal: v48a achieved 18.5% CAGR / PF 2.0 / MaxDD -5.7% / 29 trades in ~208d.
Target: CAGR >= 23%, PF > 1.5, MaxDD > -10%.

Tests exit optimizations ONLY (NOT entry relaxations):

A. Tighter trailing stop: 3x ATR → 2.5x ATR
B. Shorter max hold: 24 bars → 16 bars
C. ATR-based take profit: fixed TP at 2x ATR from entry
D. Time-based partial exit: close 50% at 8 bars, rest trails
E. Volatility filter: only enter when ATR(14) > median(ATR(14), 100)
F. Daily regime alignment: long only if daily close > daily EMA(20),
                           short only if daily close < daily EMA(20)

Then combines the best 2-3 variants.
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


# ── v48a baseline feature builder ────────────────────────────────────────────

def build_features_v48a(df):
    """Baseline: v48a logic — ADX>20, RSI pullback, candle + DI confirm."""
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
    out["long_exit"] = out["short_exit"] = False
    return out


# ── v49 variant feature builders ─────────────────────────────────────────────

def _apply_variant_flags(df, variant, daily_df=None):
    """Apply variant-specific flags to an already-built feature dataframe.

    Returns the modified dataframe.
    """
    out = df.copy()

    # E: Volatility filter — gate signals on ATR > median(ATR, 100)
    if "E" in variant:
        atr_median = out["atr14"].rolling(100, min_periods=50).median()
        out["vol_ok"] = out["atr14"] > atr_median
        out["long_signal"] = out["long_signal"] & out["vol_ok"]
        out["short_signal"] = out["short_signal"] & out["vol_ok"]

    # F: Daily regime alignment — long only if daily close > daily EMA(20)
    if "F" in variant and daily_df is not None:
        daily_ema = daily_df["close"].ewm(span=20, adjust=False).mean()
        # Map daily EMA to 1h bars: use previous day's EMA value (no look-ahead)
        daily_aligned = daily_ema.reindex(out.index, method="ffill").shift(1)
        daily_close_aligned = daily_df["close"].reindex(out.index, method="ffill").shift(1)
        out["regime_long_ok"] = daily_close_aligned > daily_aligned
        out["regime_short_ok"] = daily_close_aligned < daily_aligned
        out["long_signal"] = out["long_signal"] & out["regime_long_ok"]
        out["short_signal"] = out["short_signal"] & out["regime_short_ok"]

    return out


def build_v49_variant(df, variant, daily_df=None):
    """Build features for any v49 variant combination.

    variant: string like "A", "B", "C", "AB", "ABC", etc.
    """
    feat = build_features_v48a(df)
    feat = _apply_variant_flags(feat, variant, daily_df)
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
    max_hold_bars: int = 24
    partial_exit_bars: int = 0  # 0 = disabled, >0 = bars before partial exit
    partial_exit_pct: float = 0.5  # fraction to close at partial exit
    warmup_bars: int = 200


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


# ── engine with partial exit support ─────────────────────────────────────────

class Engine:
    def __init__(self, sc, funding, hp):
        self.sc, self.funding, self.hp = sc, funding, hp

    def run(self, df, coin=""):
        sc = self.sc
        pos, qty, entry, et, st = 0, 0.0, 0.0, None, None
        stop, trail, hw = np.nan, np.nan, np.nan
        bars, realized, trades, eq_rows = 0, 0.0, [], []
        ftot, ffees = 0.0, 0.0
        partial_done = False  # track if partial exit already fired

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
                nonlocal pos, qty, entry, et, st, stop, trail, hw, bars, realized, ftot, ffees, partial_done
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
                elif reason == "partial_exit":
                    px = apply_slippage(float(b["close"]), side, "exit", sc.exit_slippage_bps)
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

                if exit_qty is not None and exit_qty < qty:
                    # Partial exit: reduce position, keep rest running
                    qty -= exit_qty
                    partial_done = True
                else:
                    # Full exit
                    pos, qty, entry, et, st, stop, trail, hw, bars, partial_done = 0, 0.0, 0.0, None, None, np.nan, np.nan, np.nan, 0, False

            # ── Check exits ──

            # C: Take profit check (before trail stop so TP fires first if both hit)
            if sc.tp_atr_mult > 0 and pos != 0 and atr > 0:
                tp_level = entry + sc.tp_atr_mult * atr if pos == 1 else entry - sc.tp_atr_mult * atr
                if pos == 1 and b["high"] >= tp_level:
                    do_exit("take_profit"); exited = True
                elif pos == -1 and b["low"] <= tp_level:
                    do_exit("take_profit"); exited = True

            # Trail stop
            if not exited and pos == 1 and not np.isnan(trail) and b["low"] <= trail:
                do_exit("trail_stop"); exited = True
            elif not exited and pos == -1 and not np.isnan(trail) and b["high"] >= trail:
                do_exit("trail_stop"); exited = True

            # Initial stop
            if not exited and pos == 1 and not np.isnan(stop) and b["low"] <= stop:
                do_exit("stop"); exited = True
            elif not exited and pos == -1 and not np.isnan(stop) and b["high"] >= stop:
                do_exit("stop"); exited = True

            # Max hold
            if not exited and pos != 0:
                bars += 1
                if bars >= sc.max_hold_bars:
                    do_exit("max_hold"); exited = True

            # D: Partial exit at N bars
            if not exited and pos != 0 and sc.partial_exit_bars > 0 and not partial_done:
                if bars >= sc.partial_exit_bars:
                    close_qty = qty * sc.partial_exit_pct
                    do_exit("partial_exit", exit_qty=close_qty)
                    # Don't set exited=True — let remaining position continue

            # ── Entry ──
            if pos == 0:
                equity = sc.initial_capital + realized
                risk = equity * sc.risk_pct
                if bool(b.get("long_signal")) and atr > 0:
                    ep = apply_slippage(float(nb["open"]), "long", "entry", sc.entry_slippage_bps)
                    q = risk / (sc.stop_atr_mult * atr)
                    if q > 0:
                        pos, qty, entry, et, st = 1, float(q), ep, nt, t
                        stop = ep - sc.stop_atr_mult * atr
                        trail = ep - sc.trail_atr_mult * atr
                        hw = float(ep); bars = 0; partial_done = False
                elif bool(b.get("short_signal")) and atr > 0:
                    ep = apply_slippage(float(nb["open"]), "short", "entry", sc.entry_slippage_bps)
                    q = risk / (sc.stop_atr_mult * atr)
                    if q > 0:
                        pos, qty, entry, et, st = -1, float(q), ep, nt, t
                        stop = ep + sc.stop_atr_mult * atr
                        trail = ep + sc.trail_atr_mult * atr
                        hw = float(ep); bars = 0; partial_done = False

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
    }

    if "A" in variant:
        kwargs["trail_atr_mult"] = 2.5  # tighter trail
    if "B" in variant:
        kwargs["max_hold_bars"] = 16    # shorter hold
    if "C" in variant:
        kwargs["tp_atr_mult"] = 2.0     # fixed TP
    if "D" in variant:
        kwargs["partial_exit_bars"] = 8
        kwargs["partial_exit_pct"] = 0.5

    return ScenarioConfig(**kwargs)


# ── runner ───────────────────────────────────────────────────────────────────

def run_variant(variant_name, variant_flags, coin, interval, base_sc, funding, hp, daily_df, ohlcv_df, out_dir, label):
    """Run a single v49 variant."""
    bpy = bars_per_year_for(interval)
    freq = freq_for(interval)

    sc = config_for_variant(base_sc, variant_flags)
    sc.name = base_sc.name  # keep scenario name clean

    feat = build_v49_variant(ohlcv_df, variant_flags, daily_df)
    weeks = (hp.index[-1] - hp.index[0]).days / 7
    ls = int(feat.iloc[200:]["long_signal"].sum())
    ss = int(feat.iloc[200:]["short_signal"].sum())
    print(f"  [{variant_name}] Signals: {ls+ss} (L:{ls} S:{ss}) | ~{(ls+ss)/max(weeks,1):.1f}/week", end=" ")

    td, ed, costs = Engine(sc, funding, hp).run(feat, coin)
    m = calc_metrics(td, ed, sc.initial_capital, bpy)
    tpw = m["trades"] / max(weeks, 1)
    row = {"scenario": sc.name, **m, **costs, "trades_per_week": round(tpw, 2)}

    print(f"→ CAGR {m['cagr']:.1f}% PF {m['pf']:.1f} DD {m['max_dd']:.1f}% T {m['trades']}")

    if not td.empty:
        td.to_csv(out_dir / f"{label}_{variant_name}_trades.csv", index=False)
    if not ed.empty:
        ed.to_csv(out_dir / f"{label}_{variant_name}_equity.csv")

    return row


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    repo_root = Path(__file__).resolve().parents[1]
    out_dir = repo_root / "docs/backtests/v49_exit_optimization"
    out_dir.mkdir(parents=True, exist_ok=True)

    COIN = "BTC"
    INTERVAL = "1h"
    base_sc = ScenarioConfig("baseline_realistic", 0.00045, 3.0, 3.0, 8.0, "actual_signed", 1.0, "Baseline")

    # ── Fetch data once ──
    print("Fetching 1h data...")
    end_ms = now_floor_ms(INTERVAL) - 1
    earliest = infer_earliest(COIN, INTERVAL)
    if not earliest:
        raise RuntimeError(f"No {INTERVAL} data for {COIN}")
    klines_1h = fetch_klines(COIN, INTERVAL, int(earliest.timestamp()*1000), end_ms)
    rs, re, _ = longest_continuous_segment(klines_1h, freq_for(INTERVAL))
    strict_1h = klines_1h.loc[(klines_1h.index >= rs) & (klines_1h.index <= re)].copy()
    print(f"1h Window: {rs.date()} → {re.date()} ({(re-rs).days}d, {len(strict_1h)} bars)")

    funding = fetch_funding(COIN, int(rs.timestamp()*1000), int(re.timestamp()*1000))
    hp_1h = strict_1h["close"].reindex(funding.index, method="ffill")

    # Fetch daily data for variant F
    print("Fetching daily data...")
    daily_start = int((rs - pd.Timedelta(days=60)).timestamp() * 1000)
    klines_1d = fetch_klines(COIN, "1d", daily_start, end_ms)
    daily_df = klines_1d[["close"]].copy()

    # ── Define variants ──
    variants = {
        "v48a": ("", "Baseline (v48a)"),
        "A": ("A", "Tighter trail 2.5x ATR"),
        "B": ("B", "Shorter max hold 16 bars"),
        "C": ("C", "TP at 2x ATR"),
        "D": ("D", "Partial exit 50% @ 8 bars"),
        "E": ("E", "Vol filter ATR > median"),
        "F": ("F", "Daily regime EMA(20)"),
        # Combinations
        "AB": ("AB", "Tighter trail + shorter hold"),
        "AC": ("AC", "Tighter trail + TP"),
        "AE": ("AE", "Tighter trail + vol filter"),
        "BF": ("BF", "Shorter hold + regime align"),
        "ABC": ("ABC", "Tighter trail + shorter hold + TP"),
        "ABE": ("ABE", "Tighter trail + shorter hold + vol filter"),
    }

    results = {}
    for vname, (flags, desc) in variants.items():
        print(f"  [{vname}] {desc}...", end=" ")
        row = run_variant(vname, flags, COIN, INTERVAL, base_sc, funding, hp_1h, daily_df, strict_1h, out_dir, "v49")
        results[vname] = {"row": row, "desc": desc}

    # ── Summary table ──
    print("\n" + "=" * 100)
    print("  V49 EXIT OPTIMIZATION — FULL COMPARISON")
    print("=" * 100)

    cols = ["v48a", "A", "B", "C", "D", "E", "F", "AB", "AC", "AE", "BF", "ABC", "ABE"]
    header = f"  {'Metric':<16}" + "".join(f" {c:>10}" for c in cols)
    print(header)
    print("  " + "-" * (16 + 11 * len(cols)))

    for m in ["cagr", "max_dd", "pf", "wr", "trades", "trades_per_week"]:
        line = f"  {m:<16}"
        for c in cols:
            r = results.get(c, {}).get("row", {})
            val = r.get(m, "N/A")
            line += f" {str(val):>10}"
        print(line)

    # ── Verdict ──
    print(f"\n  VERDICT (target: CAGR>=23%, MaxDD>-10%, PF>1.5):")
    print(f"  {'-'*80}")

    best = None
    best_cagr = -999
    for vname in cols:
        r = results.get(vname, {}).get("row", {})
        if not r:
            continue
        cagr = r.get("cagr", 0)
        dd = r.get("max_dd", 0)
        pf = r.get("pf", 0)
        ok = "PASS" if cagr >= 23 and dd > -10 and pf > 1.5 else "FAIL"
        print(f"  {vname:>5} ({results[vname]['desc']:35s}): CAGR {cagr:>6.1f}%  DD {dd:>6.1f}%  PF {pf:>5.2f}  T {r.get('trades',0):>3}  → {ok}")
        if cagr >= 23 and dd > -10 and pf > 1.5 and cagr > best_cagr:
            best = vname
            best_cagr = cagr

    if best:
        print(f"\n  ★ BEST: {best} — reaches 23%+ CAGR with PF>1.5 and MaxDD>-10%")
    else:
        # Find highest CAGR that meets PF and DD constraints
        constrained_best = None
        constrained_cagr = -999
        for vname in cols:
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
            print(f"\n  ✗ NO variant reaches 23% CAGR with PF>1.5 and MaxDD>-10%")
            print(f"  Best constrained: {constrained_best} — CAGR {constrained_cagr:.1f}% (PF {results[constrained_best]['row']['pf']:.2f}, DD {results[constrained_best]['row']['max_dd']:.1f}%)")
            print(f"  Gap to target: {23 - constrained_cagr:.1f} percentage points")
            print(f"  → Exit optimizations alone are insufficient; entry-side improvements needed")
        else:
            # Find absolute highest CAGR regardless of constraints
            abs_best = None
            abs_cagr = -999
            for vname in cols:
                r = results.get(vname, {}).get("row", {})
                if r and r.get("cagr", 0) > abs_cagr:
                    abs_best = vname
                    abs_cagr = r["cagr"]
            print(f"\n  ✗ NO variant reaches 23% CAGR with acceptable risk")
            print(f"  Highest CAGR (any risk): {abs_best} — {abs_cagr:.1f}%")

    # ── Save summary ──
    summary = {}
    for vname in cols:
        r = results.get(vname, {}).get("row", {})
        if r:
            summary[vname] = {**r, "desc": results[vname]["desc"]}
    summary["best_variant"] = best or constrained_best or "none"
    summary["target"] = {"cagr_min": 23, "pf_min": 1.5, "max_dd_max": -10}

    (out_dir / "summary.json").write_text(json.dumps(_native(summary), indent=2))
    print(f"\n  → {out_dir}")
    print(f"  → summary.json saved")


if __name__ == "__main__":
    main()
