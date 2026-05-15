#!/usr/bin/env python3
"""Trace engine state at first divergence"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import numpy as np
import pandas as pd
from exchanges.hyperliquid.bots.btc_momentum_1h.strategy import BTCMomentum1hStrategy
from shared.python.strategy import Bar

# Inline the same data fetching as parity check
import requests
from datetime import datetime, timezone
from dataclasses import dataclass

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

# Fetch
end_ms = int(datetime.now(tz=timezone.utc).replace(minute=0, second=0, microsecond=0).timestamp()*1000) - 1
klines = fetch_klines("BTC", "1h", int(datetime(2025,10,1,tzinfo=timezone.utc).timestamp()*1000), end_ms)
rs, re, _ = longest_continuous_segment(klines, "1h")
strict = klines.loc[(klines.index >= rs) & (klines.index <= re)].copy()

# Old indicators
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
rs = g.ewm(alpha=1.0/14, adjust=False).mean() / l.ewm(alpha=1.0/14, adjust=False).mean().replace(0, np.nan)
rsi = 100 - (100 / (1 + rs))

old_df = strict.copy()
old_df["atr14"] = atr_s
old_df["adx14"] = adx
old_df["plus_di"] = plus_di
old_df["minus_di"] = minus_di
old_df["rsi14"] = rsi
old_df["rsi_rising"] = old_df["rsi14"] > old_df["rsi14"].shift(2)
old_df["rsi_falling"] = old_df["rsi14"] < old_df["rsi14"].shift(2)
old_df["rsi_pullback"] = (old_df["rsi14"] >= 35) & (old_df["rsi14"] <= 50)
old_df["rsi_overextended"] = (old_df["rsi14"] >= 50) & (old_df["rsi14"] <= 65)
old_df["bullish"] = old_df["close"] > old_df["open"]
old_df["bearish"] = old_df["close"] < old_df["open"]
old_df["adx_ok"] = old_df["adx14"] > 20
old_df["uptrend_adx"] = (old_df["plus_di"] > old_df["minus_di"]) & old_df["adx_ok"]
old_df["downtrend_adx"] = (old_df["minus_di"] > old_df["plus_di"]) & old_df["adx_ok"]
old_df["long_signal"] = old_df["uptrend_adx"] & old_df["rsi_pullback"] & old_df["bullish"] & old_df["rsi_rising"]
old_df["short_signal"] = old_df["downtrend_adx"] & old_df["rsi_overextended"] & old_df["bearish"] & old_df["rsi_falling"]

# New indicators
strategy = BTCMomentum1hStrategy()
new_df = strategy.compute_indicators(strict, pd.DataFrame())

# Simulate bar-by-bar signal generation with state seeding
new_df["long_signal"] = False
new_df["short_signal"] = False
for i in range(200):
    strategy.update_rsi_state(float(new_df.iloc[i]["rsi"]))

for i in range(200, len(new_df)):
    t = new_df.index[i]
    b = new_df.iloc[i]
    bar = Bar(time=t, open=float(b["open"]), high=float(b["high"]), low=float(b["low"]),
              close=float(b["close"]), volume=float(b["volume"]),
              indicators={"atr":float(b["atr"]),"adx":float(b["adx"]),"rsi":float(b["rsi"]),
                          "plus_di":float(b["plus_di"]),"minus_di":float(b["minus_di"])})
    sig = strategy.evaluate(bar, None)
    if sig.name == "LONG":
        new_df.loc[t, "long_signal"] = True
    elif sig.name == "SHORT":
        new_df.loc[t, "short_signal"] = True
    strategy.update_rsi_state(float(b["rsi"]))

# Find first divergence where old signal != new signal
print("Scanning for first signal divergence after warmup...\n")
for t in old_df.index[200:]:
    old_l = bool(old_df.loc[t, "long_signal"])
    old_s = bool(old_df.loc[t, "short_signal"])
    new_l = bool(new_df.loc[t, "long_signal"])
    new_s = bool(new_df.loc[t, "short_signal"])
    if old_l != new_l or old_s != new_s:
        print(f"First divergence at {t}:")
        print(f"  Old: L={old_l} S={old_s}")
        print(f"  New: L={new_l} S={new_s}")
        print(f"  ADX={old_df.loc[t,'adx14']:.2f} RSI={old_df.loc[t,'rsi14']:.2f} +DI={old_df.loc[t,'plus_di']:.2f} -DI={old_df.loc[t,'minus_di']:.2f}")
        print(f"  RSI-2={old_df.loc[t,'rsi14']-old_df.shift(2).loc[t,'rsi14']:.2f} candle={'bull' if old_df.loc[t,'close']>old_df.loc[t,'open'] else 'bear'}")
        print(f"  prev2_rsi in strategy state: {strategy._state.prev2_rsi}")
        print(f"  prev_rsi in strategy state: {strategy._state.prev_rsi}")
        break
else:
    print("No signal divergence found!")

# Also check if indicator values match at that point
print("\nIndicator value comparison (max abs diff):")
print("  ADX:", (old_df["adx14"] - new_df["adx"]).abs().max())
print("  +DI:", (old_df["plus_di"] - new_df["plus_di"]).abs().max())
print("  -DI:", (old_df["minus_di"] - new_df["minus_di"]).abs().max())
print("  RSI:", (old_df["rsi14"] - new_df["rsi"]).abs().max())
print("  ATR:", (old_df["atr14"] - new_df["atr"]).abs().max())
