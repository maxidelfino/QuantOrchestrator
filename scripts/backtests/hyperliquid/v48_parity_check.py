#!/usr/bin/env python3
"""v48 Logic Parity Check — Compare strategy.py vs standalone v48a on identical data

This fetches data ONCE, then runs:
1. Old standalone v48a build_features + Engine (from hyperliquid_v48_relaxed_filters.py)
2. New BTCMomentum1hStrategy from exchanges.hyperliquid.bots.btc_momentum_1h.strategy

Both use the SAME parameters:
  - ADX > 20
  - RSI 35-50 long / 50-65 short
  - max_hold = 16
  - risk_pct = 0.02
  - stop_atr_mult = 3.0
  - trail_atr_mult = 3.0
  - 4.5bps taker fee
  - 3bps entry/exit slippage, 8bps stop slippage
  - actual signed funding

If the restructure didn't change logic, results should be IDENTICAL.
"""

from __future__ import annotations

import sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import numpy as np
import pandas as pd
import requests
from dataclasses import dataclass
from datetime import datetime, timezone

from exchanges.hyperliquid.bots.btc_momentum_1h.strategy import BTCMomentum1hStrategy
from shared.python.strategy import Bar, Position, Signal

HYPERLIQUID_INFO = "https://api.hyperliquid.xyz/info"
_session = requests.Session()
_adapter = requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=8, max_retries=2)
_session.mount("https://", _adapter)

def dt_to_ms(s): return int(datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)
def now_floor_ms(interval):
    now = pd.Timestamp.now(tz="UTC")
    return int((now.floor("h") if interval == "1h" else now.floor(interval)).timestamp() * 1000)

def fetch_json(payload, timeout=45):
    for attempt in range(3):
        try:
            r = _session.post(HYPERLIQUID_INFO, json=payload, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except Exception:
            if attempt == 2: raise
            time.sleep(1 * (2 ** attempt))

def fetch_klines(coin, interval, start_ms, end_ms, chunk_days=90):
    out, cur, chunk_ms = [], start_ms, chunk_days * 24 * 60 * 60 * 1000
    while cur < end_ms:
        chunk_end = min(cur + chunk_ms, end_ms)
        out.extend(fetch_json({"type": "candleSnapshot", "req": {"coin": coin, "interval": interval, "startTime": cur, "endTime": chunk_end}}))
        cur = chunk_end; time.sleep(0.05)
    df = pd.DataFrame(out)
    df["t"] = pd.to_datetime(df["t"], unit="ms", utc=True)
    for c in ["o","h","l","c","v"]: df[c] = df[c].astype(float)
    df = df.rename(columns={"t":"time","o":"open","h":"high","l":"low","c":"close","v":"volume"})
    return df.set_index("time")[["open","high","low","close","volume"]].sort_index().pipe(lambda d: d[~d.index.duplicated(keep="last")])

def fetch_funding(coin, start_ms, end_ms, chunk_days=120):
    out, cur, chunk_ms = [], start_ms, chunk_days * 24 * 60 * 60 * 1000
    while cur < end_ms:
        chunk_end = min(cur + chunk_ms, end_ms)
        out.extend(fetch_json({"type": "fundingHistory", "coin": coin, "startTime": cur, "endTime": chunk_end}, timeout=60))
        cur = chunk_end + 1; time.sleep(0.05)
    if not out: return pd.DataFrame(columns=["fundingRate"])
    df = pd.DataFrame(out)
    df["time"] = pd.to_datetime(df["time"], unit="ms", utc=True)
    df["fundingRate"] = df["fundingRate"].astype(float)
    return df.set_index("time")[["fundingRate"]].sort_index().pipe(lambda d: d[~d.index.duplicated(keep="last")])

def infer_earliest(coin, interval):
    for s, e in [("2025-03-01","2025-03-31"),("2025-06-01","2025-06-30"),("2025-09-01","2025-09-30"),("2025-10-01","2025-10-31"),("2025-11-01","2025-11-30")]:
        try:
            data = fetch_json({"type": "candleSnapshot", "req": {"coin": coin, "interval": interval, "startTime": dt_to_ms(s), "endTime": dt_to_ms(e)}})
            if data: return pd.to_datetime(data[0]["t"], unit="ms", utc=True)
        except Exception: continue
    return None

def longest_continuous_segment(df, freq):
    step = pd.Timedelta(freq)
    segments, start, prev, count = [], df.index[0], df.index[0], 1
    for ts in df.index[1:]:
        if ts - prev == step: count += 1
        else: segments.append((start, prev, count)); start, count = ts, 1
        prev = ts
    segments.append((start, prev, count))
    return max(segments, key=lambda x: x[2])

# ── old v48a indicators ──────────────────────────────────────────────────────

def compute_atr_old(df, period=14):
    pc = df["close"].shift(1)
    tr = pd.concat([(df["high"] - df["low"]), (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False).mean()

def compute_adx_di_old(df, period=14):
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

def compute_rsi_old(df, period=14):
    delta = df["close"].diff()
    g, l = delta.clip(lower=0), (-delta).clip(lower=0)
    rs = g.ewm(alpha=1.0 / period, adjust=False).mean() / l.ewm(alpha=1.0 / period, adjust=False).mean().replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def build_features_v48a_old(df):
    out = df.copy()
    out["atr14"] = compute_atr_old(out, 14)
    out["adx14"], out["plus_di"], out["minus_di"] = compute_adx_di_old(out, 14)
    out["rsi14"] = compute_rsi_old(out, 14)
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

# ── shared cost model ────────────────────────────────────────────────────────

def apply_slippage(price, side, action, bps):
    bump = bps / 10_000.0
    if action == "entry": return price * (1 + bump) if side == "long" else price * (1 - bump)
    return price * (1 - bump) if side == "long" else price * (1 + bump)

def funding_pnl(funding, hourly_prices, entry_time, exit_time, side, qty, mode, mult):
    if funding.empty or mode == "none": return 0.0
    w = funding.loc[(funding.index > entry_time) & (funding.index <= exit_time)]
    if w.empty: return 0.0
    prices = hourly_prices.reindex(w.index, method="ffill")
    sign = 1.0 if side == "long" else -1.0
    signed = -sign * w["fundingRate"] * qty * prices
    if mode == "actual_signed": return float((signed * mult).sum())
    return float(signed.clip(upper=0.0).sum() * mult)

# ── old engine ───────────────────────────────────────────────────────────────

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
    trail_atr_mult: float = 3.0
    max_hold_bars: int = 16
    warmup_bars: int = 200

class OldEngine:
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

        return pd.DataFrame(trades), pd.DataFrame(eq_rows).set_index("time") if eq_rows else pd.DataFrame(columns=["equity"]), {"fees_total": round(ffees, 2), "funding_total": round(ftot, 2)}

# ── new engine (strategy.py) ─────────────────────────────────────────────────

class NewEngine:
    def __init__(self, sc, funding, hp):
        self.sc, self.funding, self.hp = sc, funding, hp

    def run(self, df, coin=""):
        sc = self.sc
        strategy = BTCMomentum1hStrategy(
            rsi_period=14, adx_period=14, adx_threshold=20.0,
            rsi_long_min=35.0, rsi_long_max=50.0,
            rsi_short_min=50.0, rsi_short_max=65.0,
            atr_period=14, stop_atr_mult=3.0, max_hold_bars=16, risk_pct=0.02,
        )
        df_ind = strategy.compute_indicators(df, pd.DataFrame())
        warmup = 200
        # Seed RSI state so prev2_rsi is valid from the first evaluated bar
        for i in range(warmup):
            strategy.update_rsi_state(float(df_ind.iloc[i]["rsi"]))
        pos, qty, entry_price, entry_time, signal_time = None, 0.0, 0.0, None, None
        stop_price, trail_price, highest, bars_held = np.nan, np.nan, np.nan, 0
        realized, trades, eq_rows = 0.0, [], []
        ftot, ffees = 0.0, 0.0

        for i in range(warmup, len(df) - 1):
            t = df.index[i]
            b = df_ind.iloc[i]
            nb = df_ind.iloc[i + 1]
            nt = df.index[i + 1]
            if any(np.isnan(b.get(c, np.nan)) for c in ["atr","adx","rsi"]):
                continue
            atr = float(b["atr"]) if not np.isnan(b["atr"]) else 0.0
            if atr <= 0: continue

            bar = Bar(time=t, open=float(b["open"]), high=float(b["high"]), low=float(b["low"]),
                      close=float(b["close"]), volume=float(b["volume"]),
                      indicators={"atr":atr,"adx":float(b["adx"]),"rsi":float(b["rsi"]),
                                  "plus_di":float(b["plus_di"]),"minus_di":float(b["minus_di"])})
            strategy.update_rsi_state(float(b["rsi"]))
            exited = False

            if pos is not None:
                if pos.side == "long":
                    highest = max(highest, float(b["high"]))
                    trail_price = highest - 3.0 * atr
                else:
                    lowest = min(highest, float(b["low"]))
                    trail_price = lowest + 3.0 * atr

            def do_exit(reason, exit_price_raw):
                nonlocal pos, qty, entry_price, entry_time, signal_time, stop_price, trail_price, highest, bars_held, realized, ftot, ffees
                side = pos.side
                px = apply_slippage(float(exit_price_raw), side, "exit",
                                    sc.stop_slippage_bps if reason in ("trail_stop","stop") else sc.exit_slippage_bps)
                fp = funding_pnl(self.funding, self.hp, entry_time, t, side, qty, sc.funding_mode, sc.funding_multiplier)
                fees = qty * entry_price * sc.fee_rate + qty * px * sc.fee_rate
                gp = qty * (px - entry_price) if side == "long" else qty * (entry_price - px)
                pnl = gp - fees + fp
                trades.append({"signal_time":signal_time,"entry_time":entry_time,"exit_time":t,"dir":side,
                               "entry":entry_price,"exit":px,"qty":qty,"gross_pnl":gp,"fees":-fees,
                               "funding":fp,"pnl":pnl,"hold_hours":(t-entry_time).total_seconds()/3600,
                               "reason":reason,"coin":coin})
                realized += pnl; ftot += fp; ffees += fees
                pos, qty, entry_price, entry_time, signal_time = None, 0.0, 0.0, None, None
                stop_price, trail_price, highest, bars_held = np.nan, np.nan, np.nan, 0

            if pos is not None:
                if pos.side == "long":
                    if b["low"] <= trail_price: do_exit("trail_stop", min(trail_price, b["open"])); exited = True
                    elif not exited and b["low"] <= stop_price: do_exit("stop", min(stop_price, b["open"])); exited = True
                else:
                    if b["high"] >= trail_price: do_exit("trail_stop", max(trail_price, b["open"])); exited = True
                    elif not exited and b["high"] >= stop_price: do_exit("stop", max(stop_price, b["open"])); exited = True
                if not exited:
                    bars_held += 1
                    if bars_held >= strategy.max_hold_bars: do_exit("max_hold", nb["open"]); exited = True

            if pos is None:
                sig = strategy.evaluate(bar, None)
                equity = sc.initial_capital + realized
                risk_usd = equity * strategy.risk_pct
                if sig == Signal.LONG and atr > 0:
                    ep = apply_slippage(float(nb["open"]), "long", "entry", sc.entry_slippage_bps)
                    dist = strategy.stop_atr_mult * atr
                    q = risk_usd / dist if dist > 0 else 0.0
                    if q > 0:
                        pos = Position(side="long", entry_price=ep, quantity=q, stop_price=ep - dist, entry_time=nt)
                        qty, entry_price, entry_time, signal_time = q, ep, nt, t
                        trail_price = ep - 3.0 * atr; highest = float(ep); bars_held = 0
                elif sig == Signal.SHORT and atr > 0:
                    ep = apply_slippage(float(nb["open"]), "short", "entry", sc.entry_slippage_bps)
                    dist = strategy.stop_atr_mult * atr
                    q = risk_usd / dist if dist > 0 else 0.0
                    if q > 0:
                        pos = Position(side="short", entry_price=ep, quantity=q, stop_price=ep + dist, entry_time=nt)
                        qty, entry_price, entry_time, signal_time = q, ep, nt, t
                        trail_price = ep + 3.0 * atr; highest = float(ep); bars_held = 0

            op = qty * (b["close"] - entry_price) if pos is not None and pos.side == "long" else (qty * (entry_price - b["close"]) if pos is not None else 0.0)
            eq_rows.append({"time": t, "equity": sc.initial_capital + realized + op})

        if pos is not None and entry_time is not None:
            t = df.index[-1]
            side = pos.side
            px = apply_slippage(float(df.iloc[-1]["close"]), side, "exit", sc.exit_slippage_bps)
            fp = funding_pnl(self.funding, self.hp, entry_time, t, side, qty, sc.funding_mode, sc.funding_multiplier)
            fees = qty * entry_price * sc.fee_rate + qty * px * sc.fee_rate
            gp = qty * (px - entry_price) if side == "long" else qty * (entry_price - px)
            pnl = gp - fees + fp
            trades.append({"signal_time":signal_time,"entry_time":entry_time,"exit_time":t,"dir":side,
                           "entry":entry_price,"exit":px,"qty":qty,"gross_pnl":gp,"fees":-fees,
                           "funding":fp,"pnl":pnl,"hold_hours":(t-entry_time).total_seconds()/3600,
                           "reason":"eod","coin":coin})

        return pd.DataFrame(trades), pd.DataFrame(eq_rows).set_index("time") if eq_rows else pd.DataFrame(columns=["equity"]), {"fees_total": round(ffees, 2), "funding_total": round(ftot, 2)}

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
    if trades.empty: pf, wr = 0, 0
    else:
        p = trades["pnl"]
        wr = float((p > 0).mean() * 100)
        w, l = p[p > 0], p[p <= 0]
        pf = float(w.sum() / abs(l.sum())) if len(l) and abs(l.sum()) > 0 else float("inf")
    return {"return_pct": round(ret,2), "cagr": round(cagr,2), "max_dd": round(dd,2),
            "pf": round(pf,2) if np.isfinite(pf) else float("inf"), "wr": round(wr,2),
            "trades": len(trades), "sharpe": round(sh,3), "sortino": round(so,3)}

# ── main ─────────────────────────────────────────────────────────────────────

def main():
    COIN = "BTC"
    INTERVAL = "1h"
    bpy = 8760
    freq = "1h"

    print("[parity] Fetching data...")
    earliest = infer_earliest(COIN, INTERVAL)
    end_ms = now_floor_ms(INTERVAL) - 1
    klines = fetch_klines(COIN, INTERVAL, int(earliest.timestamp()*1000), end_ms)
    rs, re, bar_count = longest_continuous_segment(klines, freq)
    strict = klines.loc[(klines.index >= rs) & (klines.index <= re)].copy()
    print(f"[parity] Window: {rs.date()} → {re.date()} ({(re-rs).days}d, {len(strict)} bars)")

    funding = fetch_funding(COIN, int(rs.timestamp()*1000), int(re.timestamp()*1000))
    hp = strict["close"].reindex(funding.index, method="ffill")

    sc = ScenarioConfig("baseline", 0.00045, 3.0, 3.0, 8.0, "actual_signed", 1.0, "Baseline")

    df_old = build_features_v48a_old(strict)
    td_old, ed_old, costs_old = OldEngine(sc, funding, hp).run(df_old, COIN)
    m_old = calc_metrics(td_old, ed_old, sc.initial_capital, bpy)

    td_new, ed_new, costs_new = NewEngine(sc, funding, hp).run(strict, COIN)
    m_new = calc_metrics(td_new, ed_new, sc.initial_capital, bpy)

    print("\n" + "=" * 60)
    print("  PARITY CHECK: old standalone v48a  vs  strategy.py")
    print("=" * 60)
    print(f"\n  {'Metric':<16} {'Old v48a':>14} {'strategy.py':>14} {'Δ':>10}")
    print(f"  {'-'*58}")
    for k in ["cagr","max_dd","pf","wr","trades","sharpe","sortino"]:
        o, n = m_old.get(k, 0), m_new.get(k, 0)
        d = n - o if k != "max_dd" else abs(n - o)
        print(f"  {k:<16} {o:>14} {n:>14} {d:>+10.2f}")

    # Compare trade lists
    if len(td_old) == len(td_new):
        print(f"\n  ✅ Trade counts match: {len(td_old)}")
        # Check if entry/exit times and directions match
        match = 0
        for i in range(len(td_old)):
            o, n = td_old.iloc[i], td_new.iloc[i]
            if (o["entry_time"] == n["entry_time"] and o["exit_time"] == n["exit_time"] and
                o["dir"] == n["dir"] and abs(o["pnl"] - n["pnl"]) < 0.01):
                match += 1
        print(f"  ✅ Identical trades: {match}/{len(td_old)}")
        if match == len(td_old):
            print("\n  ★ PERFECT PARITY — Restructure did NOT change logic.")
        else:
            print(f"\n  ⚠️  {len(td_old) - match} trades differ in timing or PnL.")
            # Show first mismatch
            for i in range(len(td_old)):
                o, n = td_old.iloc[i], td_new.iloc[i]
                if not (o["entry_time"] == n["entry_time"] and o["exit_time"] == n["exit_time"] and o["dir"] == n["dir"]):
                    print(f"     First mismatch at trade {i+1}:")
                    print(f"       Old: {o['dir']} entry={o['entry_time']} exit={o['exit_time']} pnl={o['pnl']:.2f}")
                    print(f"       New: {n['dir']} entry={n['entry_time']} exit={n['exit_time']} pnl={n['pnl']:.2f}")
                    break
    else:
        print(f"\n  ⚠️  Trade count MISMATCH: old={len(td_old)}, new={len(td_new)}")
        if len(td_new) > len(td_old):
            print(f"     strategy.py produced {len(td_new) - len(td_old)} MORE trades.")
        else:
            print(f"     strategy.py produced {len(td_old) - len(td_new)} FEWER trades.")

        # Find first divergence
        min_len = min(len(td_old), len(td_new))
        for i in range(min_len):
            o, n = td_old.iloc[i], td_new.iloc[i]
            if not (o["entry_time"] == n["entry_time"] and o["exit_time"] == n["exit_time"] and o["dir"] == n["dir"]):
                print(f"     First divergence at trade {i+1}:")
                print(f"       Old: {o['dir']} entry={o['entry_time']} exit={o['exit_time']}")
                print(f"       New: {n['dir']} entry={n['entry_time']} exit={n['exit_time']}")
                break
        else:
            print(f"     First {min_len} trades match; extra trades after that.")

if __name__ == "__main__":
    main()
