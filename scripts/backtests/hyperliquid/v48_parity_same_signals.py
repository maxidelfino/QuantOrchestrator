#!/usr/bin/env python3
"""Parity check USING SAME SIGNALS for both engines to isolate execution differences"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import numpy as np
import pandas as pd
from dataclasses import dataclass
from datetime import datetime, timezone
import requests

# Fetch data
HYPERLIQUID_INFO = "https://api.hyperliquid.xyz/info"
_session = requests.Session()

def fetch_json(payload, timeout=45):
    for attempt in range(3):
        try:
            r = _session.post(HYPERLIQUID_INFO, json=payload, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except Exception:
            if attempt == 2: raise

def fetch_klines(coin, interval, start_ms, end_ms, chunk_days=90):
    out, cur, chunk_ms = [], start_ms, chunk_days * 24 * 60 * 60 * 1000
    while cur < end_ms:
        chunk_end = min(cur + chunk_ms, end_ms)
        out.extend(fetch_json({"type": "candleSnapshot", "req": {"coin": coin, "interval": interval, "startTime": cur, "endTime": chunk_end}}))
        cur = chunk_end
    df = pd.DataFrame(out)
    df["t"] = pd.to_datetime(df["t"], unit="ms", utc=True)
    for c in ["o","h","l","c","v"]: df[c] = df[c].astype(float)
    df = df.rename(columns={"t":"time","o":"open","h":"high","l":"low","c":"close","v":"volume"})
    return df.set_index("time")[["open","high","low","close","volume"]].sort_index().pipe(lambda d: d[~d.index.duplicated(keep="last")])

def longest_continuous_segment(df, freq):
    step = pd.Timedelta(freq)
    segments, start, prev, count = [], df.index[0], df.index[0], 1
    for ts in df.index[1:]:
        if ts - prev == step: count += 1
        else: segments.append((start, prev, count)); start, count = ts, 1
        prev = ts
    segments.append((start, prev, count))
    return max(segments, key=lambda x: x[2])

end_ms = int(datetime.now(tz=timezone.utc).replace(minute=0, second=0, microsecond=0).timestamp()*1000) - 1
klines = fetch_klines("BTC", "1h", int(datetime(2025,10,1,tzinfo=timezone.utc).timestamp()*1000), end_ms)
rs, re, _ = longest_continuous_segment(klines, "1h")
strict = klines.loc[(klines.index >= rs) & (klines.index <= re)].copy()

# Precompute signals using old method (vectorized)
pc = strict["close"].shift(1)
tr = pd.concat([(strict["high"] - strict["low"]), (strict["high"] - pc).abs(), (strict["low"] - pc).abs()], axis=1).max(axis=1)
atr_s = tr.ewm(alpha=1.0/14, adjust=False).mean()
up, down = strict["high"] - strict["high"].shift(1), strict["low"].shift(1) - strict["low"]
plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=strict.index)
minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=strict.index)
plus_di = 100 * (plus_dm.ewm(alpha=1.0/14, adjust=False).mean() / atr_s.replace(0, np.nan))
minus_di = 100 * (minus_dm.ewm(alpha=1.0/14, adjust=False).mean() / atr_s.replace(0, np.nan))
dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di).replace(0, np.nan)
adx = dx.ewm(alpha=1.0/14, adjust=False).mean()
delta = strict["close"].diff()
g, l = delta.clip(lower=0), (-delta).clip(lower=0)
rs_ = g.ewm(alpha=1.0/14, adjust=False).mean() / l.ewm(alpha=1.0/14, adjust=False).mean().replace(0, np.nan)
rsi = 100 - (100 / (1 + rs_))

df = strict.copy()
df["atr14"] = atr_s
df["adx14"] = adx
df["plus_di"] = plus_di
df["minus_di"] = minus_di
df["rsi14"] = rsi
df["rsi_rising"] = df["rsi14"] > df["rsi14"].shift(2)
df["rsi_falling"] = df["rsi14"] < df["rsi14"].shift(2)
df["rsi_pullback"] = (df["rsi14"] >= 35) & (df["rsi14"] <= 50)
df["rsi_overextended"] = (df["rsi14"] >= 50) & (df["rsi14"] <= 65)
df["bullish"] = df["close"] > df["open"]
df["bearish"] = df["close"] < df["open"]
df["adx_ok"] = df["adx14"] > 20
df["uptrend_adx"] = (df["plus_di"] > df["minus_di"]) & df["adx_ok"]
df["downtrend_adx"] = (df["minus_di"] > df["plus_di"]) & df["adx_ok"]
df["long_signal"] = df["uptrend_adx"] & df["rsi_pullback"] & df["bullish"] & df["rsi_rising"]
df["short_signal"] = df["downtrend_adx"] & df["rsi_overextended"] & df["bearish"] & df["rsi_falling"]

# Now run two engines: old-style and new-style, both reading from SAME df signals
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

# Fetch funding
funding = fetch_json({"type": "fundingHistory", "coin": "BTC", "startTime": int(rs.timestamp()*1000), "endTime": int(re.timestamp()*1000)}, timeout=60)
if funding:
    fdf = pd.DataFrame(funding)
    fdf["time"] = pd.to_datetime(fdf["time"], unit="ms", utc=True)
    fdf["fundingRate"] = fdf["fundingRate"].astype(float)
    fdf = fdf.set_index("time")[["fundingRate"]].sort_index().pipe(lambda d: d[~d.index.duplicated(keep="last")])
else:
    fdf = pd.DataFrame(columns=["fundingRate"])
hp = strict["close"].reindex(fdf.index, method="ffill")

sc = ScenarioConfig("baseline", 0.00045, 3.0, 3.0, 8.0, "actual_signed", 1.0, "Baseline")

# Old engine
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

# New engine (same signals, different implementation style)
class NewEngine:
    def __init__(self, sc, funding, hp):
        self.sc, self.funding, self.hp = sc, funding, hp
    def run(self, df, coin=""):
        sc = self.sc
        pos_side = None  # "long" or "short"
        pos_qty = 0.0
        pos_entry = 0.0
        pos_et = None
        pos_st = None
        stop_price = np.nan
        trail_price = np.nan
        hw = np.nan
        bars_held = 0
        realized = 0.0
        trades = []
        eq_rows = []
        ftot = 0.0
        ffees = 0.0
        for i in range(sc.warmup_bars, len(df) - 1):
            t = df.index[i]
            b = df.iloc[i]
            nb = df.iloc[i + 1]
            nt = df.index[i + 1]
            if any(np.isnan(b.get(c, np.nan)) for c in ["atr14","adx14","rsi14"]):
                continue
            exited = False
            atr = float(b["atr14"]) if not np.isnan(b["atr14"]) else 0
            if pos_side == "long" and atr > 0:
                hw = max(hw, float(b["high"]))
                trail_price = hw - sc.trail_atr_mult * atr
            elif pos_side == "short" and atr > 0:
                hw = min(hw, float(b["low"]))
                trail_price = hw + sc.trail_atr_mult * atr
            def do_exit(reason):
                nonlocal pos_side, pos_qty, pos_entry, pos_et, pos_st, stop_price, trail_price, hw, bars_held, realized, ftot, ffees
                side = pos_side
                if reason == "trail_stop":
                    raw = min(float(trail_price), float(b["open"])) if side == "long" else max(float(trail_price), float(b["open"]))
                    px = apply_slippage(raw, side, "exit", sc.stop_slippage_bps)
                elif reason == "stop":
                    raw = min(float(stop_price), float(b["open"])) if side == "long" else max(float(stop_price), float(b["open"]))
                    px = apply_slippage(raw, side, "exit", sc.stop_slippage_bps)
                elif reason == "max_hold":
                    px = apply_slippage(float(nb["open"]), side, "exit", sc.exit_slippage_bps)
                else:
                    px = apply_slippage(float(b["close"]), side, "exit", sc.exit_slippage_bps)
                fp = funding_pnl(self.funding, self.hp, pos_et, t, side, pos_qty, sc.funding_mode, sc.funding_multiplier)
                fees = pos_qty * pos_entry * sc.fee_rate + pos_qty * px * sc.fee_rate
                gp = pos_qty * (px - pos_entry) if side == "long" else pos_qty * (pos_entry - px)
                pnl = gp - fees + fp
                trades.append({"signal_time": pos_st, "entry_time": pos_et, "exit_time": t, "dir": side,
                               "entry": pos_entry, "exit": px, "qty": pos_qty, "gross_pnl": gp, "fees": -fees,
                               "funding": fp, "pnl": pnl, "hold_hours": (t - pos_et).total_seconds() / 3600,
                               "reason": reason, "coin": coin})
                realized += pnl; ftot += fp; ffees += fees
                pos_side, pos_qty, pos_entry, pos_et, pos_st = None, 0.0, 0.0, None, None
                stop_price, trail_price, hw, bars_held = np.nan, np.nan, np.nan, 0
            if pos_side == "long" and b["low"] <= trail_price:
                do_exit("trail_stop"); exited = True
            elif pos_side == "short" and b["high"] >= trail_price:
                do_exit("trail_stop"); exited = True
            if not exited and pos_side == "long" and b["low"] <= stop_price:
                do_exit("stop"); exited = True
            elif not exited and pos_side == "short" and b["high"] >= stop_price:
                do_exit("stop"); exited = True
            if not exited and pos_side is not None:
                bars_held += 1
                if bars_held >= sc.max_hold_bars:
                    do_exit("max_hold"); exited = True
            if pos_side is None:
                equity = sc.initial_capital + realized
                risk = equity * sc.risk_pct
                if bool(b.get("long_signal")) and atr > 0:
                    ep = apply_slippage(float(nb["open"]), "long", "entry", sc.entry_slippage_bps)
                    q = risk / (sc.stop_atr_mult * atr)
                    if q > 0:
                        pos_side, pos_qty, pos_entry, pos_et, pos_st = "long", float(q), ep, nt, t
                        stop_price = ep - sc.stop_atr_mult * atr
                        trail_price = ep - sc.trail_atr_mult * atr
                        hw = float(ep); bars_held = 0
                elif bool(b.get("short_signal")) and atr > 0:
                    ep = apply_slippage(float(nb["open"]), "short", "entry", sc.entry_slippage_bps)
                    q = risk / (sc.stop_atr_mult * atr)
                    if q > 0:
                        pos_side, pos_qty, pos_entry, pos_et, pos_st = "short", float(q), ep, nt, t
                        stop_price = ep + sc.stop_atr_mult * atr
                        trail_price = ep + sc.trail_atr_mult * atr
                        hw = float(ep); bars_held = 0
            op = pos_qty * (b["close"] - pos_entry) if pos_side == "long" else (pos_qty * (pos_entry - b["close"]) if pos_side == "short" else 0)
            eq_rows.append({"time": t, "equity": sc.initial_capital + realized + op})
        if pos_side is not None and pos_et is not None:
            t = df.index[-1]
            side = pos_side
            px = apply_slippage(float(df.iloc[-1]["close"]), side, "exit", sc.exit_slippage_bps)
            fp = funding_pnl(self.funding, self.hp, pos_et, t, side, pos_qty, sc.funding_mode, sc.funding_multiplier)
            fees = pos_qty * pos_entry * sc.fee_rate + pos_qty * px * sc.fee_rate
            gp = pos_qty * (px - pos_entry) if side == "long" else pos_qty * (pos_entry - px)
            pnl = gp - fees + fp
            trades.append({"signal_time": pos_st, "entry_time": pos_et, "exit_time": t, "dir": side,
                           "entry": pos_entry, "exit": px, "qty": pos_qty, "gross_pnl": gp, "fees": -fees,
                           "funding": fp, "pnl": pnl, "hold_hours": (t - pos_et).total_seconds() / 3600,
                           "reason": "eod", "coin": coin})
        return pd.DataFrame(trades), pd.DataFrame(eq_rows).set_index("time") if eq_rows else pd.DataFrame(columns=["equity"]), {"fees_total": round(ffees, 2), "funding_total": round(ftot, 2)}

td_old, ed_old, _ = OldEngine(sc, fdf, hp).run(df, "BTC")
td_new, ed_new, _ = NewEngine(sc, fdf, hp).run(df, "BTC")

print(f"Old trades: {len(td_old)}, New trades: {len(td_new)}")
if len(td_old) == len(td_new):
    match = sum(1 for i in range(len(td_old)) if
                td_old.iloc[i]["entry_time"] == td_new.iloc[i]["entry_time"] and
                td_old.iloc[i]["exit_time"] == td_new.iloc[i]["exit_time"] and
                td_old.iloc[i]["dir"] == td_new.iloc[i]["dir"] and
                abs(td_old.iloc[i]["pnl"] - td_new.iloc[i]["pnl"]) < 0.01)
    print(f"Identical trades: {match}/{len(td_old)}")
    if match == len(td_old):
        print("PERFECT PARITY when using same signals!")
    else:
        for i in range(len(td_old)):
            o, n = td_old.iloc[i], td_new.iloc[i]
            if not (o["entry_time"] == n["entry_time"] and o["exit_time"] == n["exit_time"] and o["dir"] == n["dir"]):
                print(f"First diff at trade {i+1}:")
                print(f"  Old: {o['dir']} entry={o['entry_time']} exit={o['exit_time']} pnl={o['pnl']:.2f}")
                print(f"  New: {n['dir']} entry={n['entry_time']} exit={n['exit_time']} pnl={n['pnl']:.2f}")
                break
else:
    min_len = min(len(td_old), len(td_new))
    for i in range(min_len):
        o, n = td_old.iloc[i], td_new.iloc[i]
        if not (o["entry_time"] == n["entry_time"] and o["exit_time"] == n["exit_time"] and o["dir"] == n["dir"]):
            print(f"First diff at trade {i+1}:")
            print(f"  Old: {o['dir']} entry={o['entry_time']} exit={o['exit_time']} pnl={o['pnl']:.2f}")
            print(f"  New: {n['dir']} entry={n['entry_time']} exit={n['exit_time']} pnl={n['pnl']:.2f}")
            break
    else:
        print(f"First {min_len} trades match; extra trades after.")
