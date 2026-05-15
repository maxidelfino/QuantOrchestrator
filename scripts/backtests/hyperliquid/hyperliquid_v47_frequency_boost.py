#!/usr/bin/env python3
"""v47 Frequency Boost — Approaches A, B, C

Approach A: 1h timeframe, BTC only
Approach B: 2h timeframe, multi-asset (BTC+ETH+SOL+BNB)
Approach C: 1h timeframe, multi-asset (BTC+ETH+SOL+BNB)

Same v47 entry/exit logic. Only timeframe and asset universe change.
Optimized for faster API calls with session pooling.
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
    """Fetch with retries using pooled session."""
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
    """Fetch funding history with larger chunks for speed."""
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

def build_features_v47(df):
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
    out["adx_ok"] = out["adx14"] > 25
    out["uptrend_adx"] = (out["plus_di"] > out["minus_di"]) & out["adx_ok"]
    out["downtrend_adx"] = (out["minus_di"] > out["plus_di"]) & out["adx_ok"]
    out["long_signal"] = out["uptrend_adx"] & out["rsi_pullback"] & out["bullish"] & out["rsi_rising"]
    out["short_signal"] = out["downtrend_adx"] & out["rsi_overextended"] & out["bearish"] & out["rsi_falling"]
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
    max_hold_bars: int = 12
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

# ── runners ──────────────────────────────────────────────────────────────────

def run_single(coin, interval, scenarios, out_dir, label):
    bpy = bars_per_year_for(interval)
    freq = freq_for(interval)
    end_ms = now_floor_ms(interval) - 1
    print(f"  Fetching {coin} {interval}...")
    earliest = infer_earliest(coin, interval)
    if not earliest:
        raise RuntimeError(f"No {interval} data for {coin}")
    klines = fetch_klines(coin, interval, int(earliest.timestamp()*1000), end_ms)
    rs, re, _ = longest_continuous_segment(klines, freq)
    strict = klines.loc[(klines.index >= rs) & (klines.index <= re)].copy()
    print(f"  Window: {rs.date()} → {re.date()} ({(re-rs).days}d, {len(strict)} bars)")

    funding = fetch_funding(coin, int(rs.timestamp()*1000), int(re.timestamp()*1000))
    hp = strict["close"].reindex(funding.index, method="ffill")
    feat = build_features_v47(strict)
    weeks = (re - rs).days / 7
    ls = int(feat.iloc[200:]["long_signal"].sum())
    ss = int(feat.iloc[200:]["short_signal"].sum())
    print(f"  Signals: {ls+ss} (L:{ls} S:{ss}) | ~{(ls+ss)/max(weeks,1):.1f}/week")

    rows = []
    for sc in scenarios:
        print(f"  ▶ {sc.name}...", end=" ", flush=True)
        td, ed, costs = Engine(sc, funding, hp).run(feat, coin)
        m = calc_metrics(td, ed, sc.initial_capital, bpy)
        tpw = m["trades"] / max(weeks, 1)
        row = {"scenario": sc.name, **m, **costs, "trades_per_week": round(tpw, 2)}
        rows.append(row)
        print(f"CAGR {m['cagr']:.1f}% PF {m['pf']:.1f} DD {m['max_dd']:.1f}% T {m['trades']}")
        if not td.empty: td.to_csv(out_dir / f"{label}_{sc.name}_trades.csv", index=False)
        if not ed.empty: ed.to_csv(out_dir / f"{label}_{sc.name}_equity.csv")
    return rows, weeks, feat

def run_multi(coins, interval, scenarios, out_dir, label):
    bpy = bars_per_year_for(interval)
    freq = freq_for(interval)
    end_ms = now_floor_ms(interval) - 1
    print(f"  Fetching {len(coins)} coins on {interval}...")

    data, funding_map = {}, {}
    gstart, gend = None, None
    for coin in coins:
        print(f"    {coin}...", end=" ", flush=True)
        earliest = infer_earliest(coin, interval)
        if not earliest:
            print("SKIP"); continue
        kl = fetch_klines(coin, interval, int(earliest.timestamp()*1000), end_ms)
        rs, re, _ = longest_continuous_segment(kl, freq)
        strict = kl.loc[(kl.index >= rs) & (kl.index <= re)].copy()
        fund = fetch_funding(coin, int(rs.timestamp()*1000), int(re.timestamp()*1000))
        data[coin] = strict; funding_map[coin] = fund
        if gstart is None: gstart, gend = rs, re
        else: gstart, gend = max(gstart, rs), min(gend, re)
        print(f"{rs.date()}→{re.date()} ({len(strict)} bars)")

    if not data:
        raise RuntimeError("No data")
    print(f"  Common window: {gstart.date()} → {gend.date()} ({(gend-gstart).days}d)")

    for c in list(data):
        data[c] = data[c].loc[(data[c].index >= gstart) & (data[c].index <= gend)].copy()

    feats = {c: build_features_v47(df) for c, df in data.items()}
    weeks = (gend - gstart).days / 7
    for c, f in feats.items():
        ls = int(f.iloc[200:]["long_signal"].sum())
        ss = int(f.iloc[200:]["short_signal"].sum())
        print(f"    {c}: {ls+ss} signals (L:{ls} S:{ss})")

    rows = []
    for sc in scenarios:
        print(f"  ▶ {sc.name}...", end=" ", flush=True)
        all_td, all_ed, tf, tfund = [], [], 0.0, 0.0
        for c in coins:
            if c not in feats: continue
            f = funding_map.get(c, pd.DataFrame(columns=["fundingRate"]))
            hp = data[c]["close"].reindex(f.index, method="ffill") if not f.empty else pd.Series(dtype=float)
            td, ed, costs = Engine(sc, f, hp).run(feats[c], coin=c)
            if not td.empty: all_td.append(td)
            if not ed.empty: all_ed.append(ed)
            tf += costs["fees_total"]; tfund += costs["funding_total"]

        td_all = pd.concat(all_td, ignore_index=True) if all_td else pd.DataFrame()
        if all_ed:
            merged = pd.concat(all_ed).groupby("time")["equity"].sum().to_frame()
            n = len([c for c in coins if c in feats])
            merged["equity"] = merged["equity"] - (n - 1) * sc.initial_capital
            ed_all = merged
        else:
            ed_all = pd.DataFrame(columns=["equity"])

        m = calc_metrics(td_all, ed_all, sc.initial_capital, bpy)
        tpw = m["trades"] / max(weeks, 1)
        row = {"scenario": sc.name, **m, "fees_total": round(tf, 2), "funding_total": round(tfund, 2),
               "trades_per_week": round(tpw, 2)}
        rows.append(row)
        print(f"CAGR {m['cagr']:.1f}% PF {m['pf']:.1f} DD {m['max_dd']:.1f}% T {m['trades']} TPW {tpw:.2f}")
        if not td_all.empty: td_all.to_csv(out_dir / f"{label}_{sc.name}_trades.csv", index=False)
        if not ed_all.empty: ed_all.to_csv(out_dir / f"{label}_{sc.name}_equity.csv")
    return rows, weeks, feats

# ── main ─────────────────────────────────────────────────────────────────────

def main():
    repo_root = Path(__file__).resolve().parents[1]
    out_dir = repo_root / "docs/backtests/v47_frequency_boost"
    out_dir.mkdir(parents=True, exist_ok=True)

    COINS = ["BTC", "ETH", "SOL", "BNB"]
    scenarios = [ScenarioConfig("baseline_realistic", 0.00045, 3.0, 3.0, 8.0, "actual_signed", 1.0, "Baseline")]

    results = {}

    # Approach A
    print("=" * 60); print("  A: 1h BTC"); print("=" * 60)
    r, w, _ = run_single("BTC", "1h", scenarios, out_dir, "A")
    results["A"] = {"rows": r, "weeks": w, "label": "A: 1h BTC"}

    # Approach B
    print("\n" + "=" * 60); print("  B: 2h Multi (BTC+ETH+SOL+BNB)"); print("=" * 60)
    r, w, _ = run_multi(COINS, "2h", scenarios, out_dir, "B")
    results["B"] = {"rows": r, "weeks": w, "label": "B: 2h Multi"}

    # Approach C
    print("\n" + "=" * 60); print("  C: 1h Multi (BTC+ETH+SOL+BNB)"); print("=" * 60)
    r, w, _ = run_multi(COINS, "1h", scenarios, out_dir, "C")
    results["C"] = {"rows": r, "weeks": w, "label": "C: 1h Multi"}

    # Summary
    print("\n" + "=" * 60); print("  SUMMARY"); print("=" * 60)
    v47_file = repo_root / "docs/backtests/new_hyperliquid_strategy/v47_summary.json"
    v47_bl = None
    if v47_file.exists():
        v47_bl = next((r for r in json.loads(v47_file.read_text()).get("scenarios",[]) if r["scenario"]=="baseline_realistic"), None)

    bl = {}
    for k in ["A","B","C"]:
        bl[k] = next((r for r in results[k]["rows"] if r["scenario"]=="baseline_realistic"), None)

    print(f"\n  {'Metric':<16} {'v47 2hBTC':>10} {'A:1hBTC':>10} {'B:2hMulti':>10} {'C:1hMulti':>10}")
    print(f"  {'-'*60}")
    for m in ["cagr","max_dd","pf","wr","trades","trades_per_week"]:
        v = v47_bl.get(m,"N/A") if v47_bl else "N/A"
        vals = [str(bl[k].get(m,"N/A")) if bl[k] else "N/A" for k in ["A","B","C"]]
        print(f"  {m:<16} {str(v):>10} {vals[0]:>10} {vals[1]:>10} {vals[2]:>10}")

    print(f"\n  VERDICT (target: CAGR>=18%, MaxDD>-10%, PF>1.5):")
    for k in ["A","B","C"]:
        b = bl[k]
        if b:
            ok = "PASS" if b["cagr"]>=18 and b["max_dd"]>-10 and b["pf"]>1.5 else "FAIL"
            print(f"  {results[k]['label']}: CAGR {b['cagr']:.1f}% DD {b['max_dd']:.1f}% PF {b['pf']:.1f} T/wk {b['trades_per_week']:.2f} → {ok}")

    summary = {"v47_baseline": v47_bl, "approach_A": bl["A"], "approach_B": bl["B"], "approach_C": bl["C"]}
    (out_dir / "summary.json").write_text(json.dumps(_native(summary), indent=2))
    print(f"\n  → {out_dir}")

if __name__ == "__main__":
    main()
