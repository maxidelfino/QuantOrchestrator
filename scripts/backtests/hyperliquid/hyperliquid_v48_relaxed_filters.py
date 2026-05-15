#!/usr/bin/env python3
"""v48 RSI Momentum Pullback — Relaxed filters to boost trade frequency

Goal: reach 18% CAGR from v47's 15.6% by increasing trade frequency
WITHOUT destroying the edge (PF > 1.5, MaxDD < -10%).

Variants tested (all 1h BTC, same exit logic: 3x ATR trail + max 24 bars):

v48a: ADX > 20 (was 25) — minimal change, allows weaker trends
v48b: ADX > 20 + wider RSI (long: 30-55, short: 45-70)
v48c: ADX > 20 + wider RSI + NO candle direction filter

Kept from v47:
- RSI momentum confirmation (rising/falling vs 2 bars ago)
- DI confirmation (+DI > -DI for long, -DI > +DI for short)
- 3x ATR trailing stop, max 24 bars hold
- Next-bar execution, 4.5bps taker, hourly funding
"""

from __future__ import annotations

import json, math, time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

HYPERLIQUID_INFO = "https://api.hyperliquid.xyz/info"
_session = requests.Session()
_adapter = requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=8, max_retries=2)
_session.mount("https://", _adapter)

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

# ── v48 feature builders (3 variants) ────────────────────────────────────────

def build_features_v48a(df):
    """v48a: ADX > 20 only (was 25). Everything else same as v47."""
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
    out["adx_ok"] = out["adx14"] > 20  # ← relaxed from 25
    out["uptrend_adx"] = (out["plus_di"] > out["minus_di"]) & out["adx_ok"]
    out["downtrend_adx"] = (out["minus_di"] > out["plus_di"]) & out["adx_ok"]
    out["long_signal"] = out["uptrend_adx"] & out["rsi_pullback"] & out["bullish"] & out["rsi_rising"]
    out["short_signal"] = out["downtrend_adx"] & out["rsi_overextended"] & out["bearish"] & out["rsi_falling"]
    out["long_exit"] = out["short_exit"] = False
    return out

def build_features_v48b(df):
    """v48b: ADX > 20 + wider RSI ranges (long: 30-55, short: 45-70)."""
    out = df.copy()
    out["atr14"] = compute_atr(out, 14)
    out["adx14"], out["plus_di"], out["minus_di"] = compute_adx_di(out, 14)
    out["rsi14"] = compute_rsi(out, 14)
    out["rsi_rising"] = out["rsi14"] > out["rsi14"].shift(2)
    out["rsi_falling"] = out["rsi14"] < out["rsi14"].shift(2)
    out["rsi_pullback"] = (out["rsi14"] >= 30) & (out["rsi14"] <= 55)   # ← wider
    out["rsi_overextended"] = (out["rsi14"] >= 45) & (out["rsi14"] <= 70)  # ← wider
    out["bullish"] = out["close"] > out["open"]
    out["bearish"] = out["close"] < out["open"]
    out["adx_ok"] = out["adx14"] > 20
    out["uptrend_adx"] = (out["plus_di"] > out["minus_di"]) & out["adx_ok"]
    out["downtrend_adx"] = (out["minus_di"] > out["plus_di"]) & out["adx_ok"]
    out["long_signal"] = out["uptrend_adx"] & out["rsi_pullback"] & out["bullish"] & out["rsi_rising"]
    out["short_signal"] = out["downtrend_adx"] & out["rsi_overextended"] & out["bearish"] & out["rsi_falling"]
    out["long_exit"] = out["short_exit"] = False
    return out

def build_features_v48c(df):
    """v48c: ADX > 20 + wider RSI + NO candle direction filter."""
    out = df.copy()
    out["atr14"] = compute_atr(out, 14)
    out["adx14"], out["plus_di"], out["minus_di"] = compute_adx_di(out, 14)
    out["rsi14"] = compute_rsi(out, 14)
    out["rsi_rising"] = out["rsi14"] > out["rsi14"].shift(2)
    out["rsi_falling"] = out["rsi14"] < out["rsi14"].shift(2)
    out["rsi_pullback"] = (out["rsi14"] >= 30) & (out["rsi14"] <= 55)
    out["rsi_overextended"] = (out["rsi14"] >= 45) & (out["rsi14"] <= 70)
    # NO bullish/bearish candle filter
    out["adx_ok"] = out["adx14"] > 20
    out["uptrend_adx"] = (out["plus_di"] > out["minus_di"]) & out["adx_ok"]
    out["downtrend_adx"] = (out["minus_di"] > out["plus_di"]) & out["adx_ok"]
    out["long_signal"] = out["uptrend_adx"] & out["rsi_pullback"] & out["rsi_rising"]  # ← no & out["bullish"]
    out["short_signal"] = out["downtrend_adx"] & out["rsi_overextended"] & out["rsi_falling"]  # ← no & out["bearish"]
    out["long_exit"] = out["short_exit"] = False
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
    stop_atr_mult: float = 3.0
    trail_atr_mult: float = 3.0
    tp_atr_mult: float = 0.0
    max_hold_bars: int = 24  # 24h on 1h = 24 bars
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

# ── engine ───────────────────────────────────────────────────────────────────

class Engine:
    def __init__(self, sc, funding, hp):
        self.sc, self.funding, self.hp = sc, funding, hp

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

            if pos == 1 and atr > 0:
                hw = max(hw, float(b["high"]))
                trail = hw - sc.trail_atr_mult * atr
            elif pos == -1 and atr > 0:
                hw = min(hw, float(b["low"]))
                trail = hw + sc.trail_atr_mult * atr

            def do_exit(reason):
                nonlocal pos, qty, entry, et, st, stop, trail, hw, bars, realized, ftot, ffees
                side = "long" if pos == 1 else "short"
                if reason == "trail_stop":
                    raw = min(float(trail), float(b["open"])) if pos == 1 else max(float(trail), float(b["open"]))
                    px = apply_slippage(raw, side, "exit", sc.stop_slippage_bps)
                elif reason == "stop":
                    raw = min(float(stop), float(b["open"])) if pos == 1 else max(float(stop), float(b["open"]))
                    px = apply_slippage(raw, side, "exit", sc.stop_slippage_bps)
                elif reason == "max_hold":
                    px = apply_slippage(float(nb["open"]), side, "exit", sc.exit_slippage_bps)
                else:
                    px = apply_slippage(float(b["close"]), side, "exit", sc.exit_slippage_bps)

                fp = funding_pnl(self.funding, self.hp, et, t, side, qty, sc.funding_mode, sc.funding_multiplier)
                fees = qty * entry * sc.fee_rate + qty * px * sc.fee_rate
                gp = qty * (px - entry) if pos == 1 else qty * (entry - px)
                pnl = gp - fees + fp
                trades.append({"signal_time": st, "entry_time": et, "exit_time": t, "dir": side,
                               "entry": entry, "exit": px, "qty": qty, "gross_pnl": gp, "fees": -fees,
                               "funding": fp, "pnl": pnl, "hold_hours": (t - et).total_seconds() / 3600,
                               "reason": reason, "coin": coin})
                realized += pnl; ftot += fp; ffees += fees
                pos, qty, entry, et, st, stop, trail, hw, bars = 0, 0.0, 0.0, None, None, np.nan, np.nan, np.nan, 0

            if pos == 1 and b["low"] <= trail:
                do_exit("trail_stop"); exited = True
            elif pos == -1 and b["high"] >= trail:
                do_exit("trail_stop"); exited = True
            if not exited and pos == 1 and b["low"] <= stop:
                do_exit("stop"); exited = True
            elif not exited and pos == -1 and b["high"] >= stop:
                do_exit("stop"); exited = True
            if not exited and pos != 0:
                bars += 1
                if bars >= sc.max_hold_bars:
                    do_exit("max_hold"); exited = True

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
                        hw = float(ep); bars = 0
                elif bool(b.get("short_signal")) and atr > 0:
                    ep = apply_slippage(float(nb["open"]), "short", "entry", sc.entry_slippage_bps)
                    q = risk / (sc.stop_atr_mult * atr)
                    if q > 0:
                        pos, qty, entry, et, st = -1, float(q), ep, nt, t
                        stop = ep + sc.stop_atr_mult * atr
                        trail = ep + sc.trail_atr_mult * atr
                        hw = float(ep); bars = 0

            op = qty * (b["close"] - entry) if pos == 1 else (qty * (entry - b["close"]) if pos == -1 else 0)
            eq_rows.append({"time": t, "equity": sc.initial_capital + realized + op})

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

# ── runner ───────────────────────────────────────────────────────────────────

def run_variant(variant_name, build_fn, coin, interval, scenarios, out_dir, label):
    """Run a single v48 variant."""
    bpy = bars_per_year_for(interval)
    freq = freq_for(interval)
    end_ms = now_floor_ms(interval) - 1
    print(f"  [{variant_name}] Fetching {coin} {interval}...")
    earliest = infer_earliest(coin, interval)
    if not earliest:
        raise RuntimeError(f"No {interval} data for {coin}")
    klines = fetch_klines(coin, interval, int(earliest.timestamp()*1000), end_ms)
    rs, re, _ = longest_continuous_segment(klines, freq)
    strict = klines.loc[(klines.index >= rs) & (klines.index <= re)].copy()
    print(f"  [{variant_name}] Window: {rs.date()} → {re.date()} ({(re-rs).days}d, {len(strict)} bars)")

    funding = fetch_funding(coin, int(rs.timestamp()*1000), int(re.timestamp()*1000))
    hp = strict["close"].reindex(funding.index, method="ffill")
    feat = build_fn(strict)
    weeks = (re - rs).days / 7
    ls = int(feat.iloc[200:]["long_signal"].sum())
    ss = int(feat.iloc[200:]["short_signal"].sum())
    print(f"  [{variant_name}] Signals: {ls+ss} (L:{ls} S:{ss}) | ~{(ls+ss)/max(weeks,1):.1f}/week")

    rows = []
    for sc in scenarios:
        print(f"  [{variant_name}] ▶ {sc.name}...", end=" ", flush=True)
        td, ed, costs = Engine(sc, funding, hp).run(feat, coin)
        m = calc_metrics(td, ed, sc.initial_capital, bpy)
        tpw = m["trades"] / max(weeks, 1)
        row = {"scenario": sc.name, **m, **costs, "trades_per_week": round(tpw, 2)}
        rows.append(row)
        print(f"CAGR {m['cagr']:.1f}% PF {m['pf']:.1f} DD {m['max_dd']:.1f}% T {m['trades']}")
        if not td.empty: td.to_csv(out_dir / f"{label}_{variant_name}_{sc.name}_trades.csv", index=False)
        if not ed.empty: ed.to_csv(out_dir / f"{label}_{variant_name}_{sc.name}_equity.csv")
    return rows, weeks, feat

# ── main ─────────────────────────────────────────────────────────────────────

def main():
    repo_root = Path(__file__).resolve().parents[1]
    out_dir = repo_root / "docs/backtests/v48_relaxed_filters"
    out_dir.mkdir(parents=True, exist_ok=True)

    COIN = "BTC"
    INTERVAL = "1h"
    scenarios = [ScenarioConfig("baseline_realistic", 0.00045, 3.0, 3.0, 8.0, "actual_signed", 1.0, "Baseline")]

    variants = {
        "v48a": ("ADX>20 only", build_features_v48a),
        "v48b": ("ADX>20 + wider RSI", build_features_v48b),
        "v48c": ("ADX>20 + wider RSI + no candle", build_features_v48c),
    }

    results = {}
    for vname, (desc, bfn) in variants.items():
        print("=" * 60)
        print(f"  {vname}: {desc}")
        print("=" * 60)
        r, w, feat = run_variant(vname, bfn, COIN, INTERVAL, scenarios, out_dir, "v48")
        results[vname] = {"rows": r, "weeks": w, "desc": desc}

    # ── Summary table ─────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("  COMPARISON TABLE")
    print("=" * 70)

    # v47 1h baseline from previous run
    v47_file = repo_root / "docs/backtests/v47_frequency_boost/summary.json"
    v47_bl = None
    if v47_file.exists():
        v47_bl = json.loads(v47_file.read_text()).get("approach_A")

    print(f"\n  {'Metric':<16} {'v47 1h':>10} {'v48a':>10} {'v48b':>10} {'v48c':>10}")
    print(f"  {'-'*60}")
    for m in ["cagr", "max_dd", "pf", "wr", "trades", "trades_per_week"]:
        v47v = v47_bl.get(m, "N/A") if v47_bl else "N/A"
        vals = []
        for vn in ["v48a", "v48b", "v48c"]:
            bl = next((r for r in results[vn]["rows"] if r["scenario"] == "baseline_realistic"), None)
            vals.append(str(bl.get(m, "N/A")) if bl else "N/A")
        print(f"  {m:<16} {str(v47v):>10} {vals[0]:>10} {vals[1]:>10} {vals[2]:>10}")

    # ── Verdict ────────────────────────────────────────────────────────────
    print(f"\n  VERDICT (target: CAGR>=18%, MaxDD>-10%, PF>1.5):")
    best = None
    best_cagr = -999
    for vn in ["v48a", "v48b", "v48c"]:
        bl = next((r for r in results[vn]["rows"] if r["scenario"] == "baseline_realistic"), None)
        if bl:
            ok = "PASS" if bl["cagr"] >= 18 and bl["max_dd"] > -10 and bl["pf"] > 1.5 else "FAIL"
            print(f"  {vn} ({results[vn]['desc']}): CAGR {bl['cagr']:.1f}% DD {bl['max_dd']:.1f}% PF {bl['pf']:.1f} T/wk {bl['trades_per_week']:.2f} → {ok}")
            if bl["cagr"] >= 18 and bl["max_dd"] > -10 and bl["pf"] > 1.5 and bl["cagr"] > best_cagr:
                best = vn
                best_cagr = bl["cagr"]

    if best:
        print(f"\n  ★ BEST: {best} — reaches 18% CAGR with acceptable risk")
    else:
        print(f"\n  ✗ NO variant reaches 18% CAGR with PF>1.5 and MaxDD>-10%")
        # Pick the one closest to target
        closest = None
        closest_score = -999
        for vn in ["v48a", "v48b", "v48c"]:
            bl = next((r for r in results[vn]["rows"] if r["scenario"] == "baseline_realistic"), None)
            if bl:
                score = bl["cagr"] + (bl["pf"] if bl["pf"] < 10 else 10) + (bl["max_dd"] if bl["max_dd"] > -10 else -10)
                if score > closest_score:
                    closest = vn
                    closest_score = score
        if closest:
            print(f"  Closest: {closest} — may need further tuning")

    # ── Save summary ──────────────────────────────────────────────────────
    summary = {
        "v47_1h_baseline": v47_bl,
        "v48a": next((r for r in results["v48a"]["rows"] if r["scenario"] == "baseline_realistic"), None),
        "v48b": next((r for r in results["v48b"]["rows"] if r["scenario"] == "baseline_realistic"), None),
        "v48c": next((r for r in results["v48c"]["rows"] if r["scenario"] == "baseline_realistic"), None),
        "best_variant": best or "none",
    }
    (out_dir / "summary.json").write_text(json.dumps(_native(summary), indent=2))
    print(f"\n  → {out_dir}")

if __name__ == "__main__":
    main()
