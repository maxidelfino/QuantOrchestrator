#!/usr/bin/env python3
"""Instrumented parity check that traces every bar until divergence"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import numpy as np
import pandas as pd
from dataclasses import dataclass
from datetime import datetime, timezone
import requests

from exchanges.hyperliquid.bots.btc_momentum_1h.strategy import BTCMomentum1hStrategy
from shared.python.strategy import Bar, Position, Signal

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

# Old features
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

df_old = strict.copy()
df_old["atr14"] = atr_s
df_old["adx14"] = adx
df_old["plus_di"] = plus_di
df_old["minus_di"] = minus_di
df_old["rsi14"] = rsi
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

# New indicators
strategy = BTCMomentum1hStrategy()
df_new = strategy.compute_indicators(strict, pd.DataFrame())

# Seed state
for i in range(200):
    strategy.update_rsi_state(float(df_new.iloc[i]["rsi"]))

# Now run both engines side-by-side, printing state at each bar
warmup = 200
old_pos, new_pos = 0, None
old_trades, new_trades = [], []
old_realized, new_realized = 0.0, 0.0
old_qty, new_qty = 0.0, 0.0
old_entry, new_entry = 0.0, 0.0
old_stop, new_stop = np.nan, np.nan
old_trail, new_trail = np.nan, np.nan
old_hw, new_hw = np.nan, np.nan
old_bars, new_bars = 0, 0

for i in range(warmup, len(strict) - 1):
    t = strict.index[i]
    b_old = df_old.iloc[i]
    b_new = df_new.iloc[i]
    
    old_atr = float(b_old["atr14"]) if not np.isnan(b_old["atr14"]) else 0
    new_atr = float(b_new["atr"]) if not np.isnan(b_new["atr"]) else 0
    
    old_long = bool(b_old["long_signal"])
    old_short = bool(b_old["short_signal"])
    
    bar = Bar(time=t, open=float(b_new["open"]), high=float(b_new["high"]), low=float(b_new["low"]),
              close=float(b_new["close"]), volume=float(b_new["volume"]),
              indicators={"atr":new_atr,"adx":float(b_new["adx"]),"rsi":float(b_new["rsi"]),
                          "plus_di":float(b_new["plus_di"]),"minus_di":float(b_new["minus_di"])})
    new_sig = strategy.evaluate(bar, None)
    strategy.update_rsi_state(float(b_new["rsi"]))
    
    new_long = new_sig == Signal.LONG
    new_short = new_sig == Signal.SHORT
    
    if old_long != new_long or old_short != new_short:
        print(f"\n*** SIGNAL DIVERGENCE at {t} ***")
        print(f"  Old: L={old_long} S={old_short}")
        print(f"  New: L={new_long} S={new_short}")
        print(f"  Old indicators: ADX={b_old['adx14']:.2f} RSI={b_old['rsi14']:.2f} +DI={b_old['plus_di']:.2f} -DI={b_old['minus_di']:.2f}")
        print(f"  New indicators: ADX={b_new['adx']:.2f} RSI={b_new['rsi']:.2f} +DI={b_new['plus_di']:.2f} -DI={b_new['minus_di']:.2f}")
        print(f"  Old: rsi_rising={b_old['rsi_rising']} bullish={b_old['bullish']}")
        print(f"  New: prev2_rsi={strategy._state.prev2_rsi} prev_rsi={strategy._state.prev_rsi}")
        break
    
    # Simulate old engine exit
    old_exited = False
    if old_pos == 1 and old_atr > 0:
        old_hw = max(old_hw, float(b_old["high"]))
        old_trail = old_hw - 3.0 * old_atr
    elif old_pos == -1 and old_atr > 0:
        old_hw = min(old_hw, float(b_old["low"]))
        old_trail = old_hw + 3.0 * old_atr
    
    if old_pos == 1 and b_old["low"] <= old_trail:
        old_pos = 0; old_exited = True
    elif old_pos == -1 and b_old["high"] >= old_trail:
        old_pos = 0; old_exited = True
    if not old_exited and old_pos == 1 and b_old["low"] <= old_stop:
        old_pos = 0; old_exited = True
    elif not old_exited and old_pos == -1 and b_old["high"] >= old_stop:
        old_pos = 0; old_exited = True
    if not old_exited and old_pos != 0:
        old_bars += 1
        if old_bars >= 16:
            old_pos = 0; old_exited = True
    
    # Simulate new engine exit
    new_exited = False
    if new_pos == "long" and new_atr > 0:
        new_hw = max(new_hw, float(b_new["high"]))
        new_trail = new_hw - 3.0 * new_atr
    elif new_pos == "short" and new_atr > 0:
        new_hw = min(new_hw, float(b_new["low"]))
        new_trail = new_hw + 3.0 * new_atr
    
    if new_pos == "long" and b_new["low"] <= new_trail:
        new_pos = None; new_exited = True
    elif new_pos == "short" and b_new["high"] >= new_trail:
        new_pos = None; new_exited = True
    if not new_exited and new_pos == "long" and b_new["low"] <= new_stop:
        new_pos = None; new_exited = True
    elif not new_exited and new_pos == "short" and b_new["high"] >= new_stop:
        new_pos = None; new_exited = True
    if not new_exited and new_pos is not None:
        new_bars += 1
        if new_bars >= 16:
            new_pos = None; new_exited = True
    
    if old_pos == 0 and new_pos is None:
        # Both flat, check entries
        if old_long != new_long or old_short != new_short:
            # Already caught above
            pass
        if old_long:
            old_pos = 1
        elif old_short:
            old_pos = -1
        if new_long:
            new_pos = "long"
        elif new_short:
            new_pos = "short"
    elif (old_pos == 0) != (new_pos is None):
        print(f"\n*** POSITION STATE DIVERGENCE at {t} ***")
        print(f"  Old pos: {old_pos}, New pos: {new_pos}")
        print(f"  Old exited: {old_exited}, New exited: {new_exited}")
        break
else:
    print("No divergence found in signal or position state!")
