#!/usr/bin/env python3
"""Debug: compare signal generation bar-by-bar between old v48a and strategy.py"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import numpy as np
import pandas as pd
from exchanges.hyperliquid.bots.btc_momentum_1h.strategy import BTCMomentum1hStrategy
from shared.python.strategy import Bar

# Use already-downloaded data from parity check run
# We'll just fetch a small window around the divergence

import requests
from datetime import datetime, timezone

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

# Fetch a small window: 2025-10-15 to 2025-11-05
start_ms = int(datetime(2025,10,15,tzinfo=timezone.utc).timestamp()*1000)
end_ms   = int(datetime(2025,11, 5,tzinfo=timezone.utc).timestamp()*1000)
data = fetch_json({"type": "candleSnapshot", "req": {"coin": "BTC", "interval": "1h", "startTime": start_ms, "endTime": end_ms}})
df = pd.DataFrame(data)
df["t"] = pd.to_datetime(df["t"], unit="ms", utc=True)
for c in ["o","h","l","c","v"]: df[c] = df[c].astype(float)
df = df.rename(columns={"t":"time","o":"open","h":"high","l":"low","c":"close","v":"volume"})
df = df.set_index("time")[["open","high","low","close","volume"]].sort_index().pipe(lambda d: d[~d.index.duplicated(keep="last")])

# ── old indicators ───────────────────────────────────────────────────────────
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

df_old = df.copy()
df_old["atr14"] = compute_atr_old(df_old, 14)
df_old["adx14"], df_old["plus_di"], df_old["minus_di"] = compute_adx_di_old(df_old, 14)
df_old["rsi14"] = compute_rsi_old(df_old, 14)
df_old["rsi_rising"] = df_old["rsi14"] > df_old["rsi14"].shift(2)
df_old["rsi_falling"] = df_old["rsi14"] < df_old["rsi14"].shift(2)
df_old["rsi_pullback"] = (df_old["rsi14"] >= 35) & (df_old["rsi14"] <= 50)
df_old["rsi_overextended"] = (df_old["rsi14"] >= 50) & (df_old["rsi14"] <= 65)
df_old["bullish"] = df_old["close"] > df_old["open"]
df_old["bearish"] = df_old["close"] < df_old["open"]
df_old["adx_ok"] = df_old["adx14"] > 20
df_old["uptrend_adx"] = (df_old["plus_di"] > df_old["minus_di"]) & df_old["adx_ok"]
df_old["downtrend_adx"] = (df_old["minus_di"] > df_old["plus_di"]) & df_old["adx_ok"]
df_old["long_signal"] = df_old["uptrend_adx"] & df_old["rsi_pullback"] & df_old["bullish"] & df_old["rsi_rising"]
df_old["short_signal"] = df_old["downtrend_adx"] & df_old["rsi_overextended"] & df_old["bearish"] & df_old["rsi_falling"]

# ── new indicators ───────────────────────────────────────────────────────────
strategy = BTCMomentum1hStrategy()
df_new = strategy.compute_indicators(df, pd.DataFrame())

# Simulate signal generation with state
df_new["long_signal"] = False
df_new["short_signal"] = False
state_prev = None
state_prev2 = None
for i in range(len(df_new)):
    t = df_new.index[i]
    b = df_new.iloc[i]
    if any(np.isnan(b.get(c, np.nan)) for c in ["atr","adx","rsi","plus_di","minus_di"]):
        state_prev2 = state_prev
        state_prev = b["rsi"]
        continue
    bar = Bar(time=t, open=float(b["open"]), high=float(b["high"]), low=float(b["low"]),
              close=float(b["close"]), volume=float(b["volume"]),
              indicators={"atr":float(b["atr"]),"adx":float(b["adx"]),"rsi":float(b["rsi"]),
                          "plus_di":float(b["plus_di"]),"minus_di":float(b["minus_di"])})
    sig = strategy.evaluate(bar, None)
    if sig.name == "LONG":
        df_new.loc[t, "long_signal"] = True
    elif sig.name == "SHORT":
        df_new.loc[t, "short_signal"] = True
    state_prev2 = state_prev
    state_prev = b["rsi"]

# ── compare ──────────────────────────────────────────────────────────────────
print("Comparing signals (showing first 20 divergences)...\n")
print(f"{'Time':<22} {'Old L':>5} {'Old S':>5} {'New L':>5} {'New S':>5} {'ADX':>6} {'RSI':>6} {'+DI':>6} {'-DI':>6} {'RSIΔ2':>6} {'Candle':>7}")
print("-" * 100)
count = 0
for t in df_old.index:
    old_l = bool(df_old.loc[t, "long_signal"])
    old_s = bool(df_old.loc[t, "short_signal"])
    new_l = bool(df_new.loc[t, "long_signal"])
    new_s = bool(df_new.loc[t, "short_signal"])
    if old_l != new_l or old_s != new_s:
        count += 1
        if count <= 20:
            rsi = df_old.loc[t, "rsi14"]
            adx = df_old.loc[t, "adx14"]
            pdi = df_old.loc[t, "plus_di"]
            mdi = df_old.loc[t, "minus_di"]
            rsi2 = df_old.loc[t, "rsi14"] - df_old.loc[t, "rsi14"] if pd.isna(df_old.loc[t, "rsi14"]) else df_old.loc[t, "rsi14"] - (df_old.shift(2).loc[t, "rsi14"] if not pd.isna(df_old.shift(2).loc[t, "rsi14"]) else 0)
            candle = "bull" if df_old.loc[t, "close"] > df_old.loc[t, "open"] else "bear" if df_old.loc[t, "close"] < df_old.loc[t, "open"] else "doji"
            print(f"{str(t):<22} {old_l!s:>5} {old_s!s:>5} {new_l!s:>5} {new_s!s:>5} {adx:>6.2f} {rsi:>6.2f} {pdi:>6.2f} {mdi:>6.2f} {rsi2:>+6.2f} {candle:>7}")

print(f"\nTotal diverging bars: {count}")

# Also compare indicator values
print("\nIndicator value diffs (max abs):")
print("  ADX:", (df_old["adx14"] - df_new["adx"]).abs().max())
print("  +DI:", (df_old["plus_di"] - df_new["plus_di"]).abs().max())
print("  -DI:", (df_old["minus_di"] - df_new["minus_di"]).abs().max())
print("  RSI:", (df_old["rsi14"] - df_new["rsi"]).abs().max())
print("  ATR:", (df_old["atr14"] - df_new["atr"]).abs().max())
