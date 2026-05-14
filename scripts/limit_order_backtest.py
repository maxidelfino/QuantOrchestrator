#!/usr/bin/env python3
"""Limit Order Backtest — v40 (btc_trend_4h) vs v48b (btc_momentum_1h)

Simulates limit order entries vs market orders to measure:
- Fee savings from maker vs taker rates
- Fill miss rate impact (fallback to market next bar)
- Net performance delta

Limit order model:
- Limit placed at signal bar CLOSE price
- Fill probability: configurable per scenario
- If filled: maker fee (1.0 bps), entry at limit price
- If missed: fallback to next bar open with market order (taker 4.5 bps + 1bp slippage)
- Exits always use market orders (stops/trails can't be limit)

Scenarios per bot:
- Optimistic: 80% fill rate
- Baseline: 70% (v40) / 60% (v48b)
- Stressed: 50% fill rate
"""

from __future__ import annotations

import json, math, time, hashlib
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

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
    elif interval == "4h":
        floored_hour = (now.hour // 4) * 4
        floored = now.floor("D") + pd.Timedelta(hours=floored_hour)
    else:
        floored = now.floor(interval)
    return int(floored.timestamp() * 1000)

def bars_per_year(interval: str) -> int:
    return {"1h": 8760, "2h": 4380, "4h": 2190, "1d": 365}.get(interval, 4380)

def freq_for(interval: str) -> str:
    return {"1h": "1h", "4h": "4h", "1d": "1D"}.get(interval, interval)

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
    if interval == "4h":
        probes = [("2021-01-01", "2021-12-31"), ("2022-01-01", "2022-12-31"), ("2023-01-01", "2023-12-31")]
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

# ── feature builders ─────────────────────────────────────────────────────────

def build_v40_features(df_4h, df_daily, warmup_daily_start="2021-01-01"):
    """v40: EMA50/200 trend + daily EMA200 regime filter (4h)"""
    out = df_4h.copy()
    out["ema50"] = out["close"].ewm(span=50, adjust=False).mean()
    out["ema200"] = out["close"].ewm(span=200, adjust=False).mean()
    out["atr14"] = compute_atr(out, 14)

    daily = df_daily.copy()
    daily = daily.loc[daily.index >= pd.Timestamp(warmup_daily_start, tz="UTC")].copy()
    daily["ema200_daily"] = daily["close"].ewm(span=200, adjust=False).mean()
    daily["ema200_daily_completed"] = daily["ema200_daily"].shift(1)
    regime = daily[["ema200_daily_completed"]].reindex(out.index, method="ffill")
    out["ema200_daily"] = regime["ema200_daily_completed"]

    out["regime_long"] = (out["close"] > out["ema200_daily"]).astype(int)
    out["regime_short"] = (out["close"] < out["ema200_daily"]).astype(int)
    out["long_signal"] = (out["close"] > out["ema50"]) & (out["ema50"] > out["ema200"]) & (out["regime_long"] == 1)
    out["short_signal"] = (out["close"] < out["ema50"]) & (out["ema50"] < out["ema200"]) & (out["regime_short"] == 1)
    out["long_exit_signal"] = out["ema50"] < out["ema200"]
    out["short_exit_signal"] = out["ema50"] > out["ema200"]
    return out

def build_v48b_features(df):
    """v48b: RSI pullback + ADX>20 + wider RSI ranges (1h)"""
    out = df.copy()
    out["atr14"] = compute_atr(out, 14)
    out["adx14"], out["plus_di"], out["minus_di"] = compute_adx_di(out, 14)
    out["rsi14"] = compute_rsi(out, 14)
    out["rsi_rising"] = out["rsi14"] > out["rsi14"].shift(2)
    out["rsi_falling"] = out["rsi14"] < out["rsi14"].shift(2)
    out["rsi_pullback"] = (out["rsi14"] >= 30) & (out["rsi14"] <= 55)
    out["rsi_overextended"] = (out["rsi14"] >= 45) & (out["rsi14"] <= 70)
    out["bullish"] = out["close"] > out["open"]
    out["bearish"] = out["close"] < out["open"]
    out["adx_ok"] = out["adx14"] > 20
    out["uptrend_adx"] = (out["plus_di"] > out["minus_di"]) & out["adx_ok"]
    out["downtrend_adx"] = (out["minus_di"] > out["plus_di"]) & out["adx_ok"]
    out["long_signal"] = out["uptrend_adx"] & out["rsi_pullback"] & out["bullish"] & out["rsi_rising"]
    out["short_signal"] = out["downtrend_adx"] & out["rsi_overextended"] & out["bearish"] & out["rsi_falling"]
    out["long_exit"] = out["short_exit"] = False
    return out

# ── cost model ───────────────────────────────────────────────────────────────

def apply_slippage(price, side, action, bps):
    bump = bps / 10_000.0
    if action == "entry":
        return price * (1 + bump) if side == "long" else price * (1 - bump)
    return price * (1 - bump) if side == "long" else price * (1 + bump)

def funding_pnl(funding, hp, entry_time, exit_time, side, qty, mode, mult):
    if funding.empty:
        return 0.0
    w = funding.loc[(funding.index > entry_time) & (funding.index <= exit_time)]
    if w.empty:
        return 0.0
    prices = hp.reindex(w.index, method="ffill")
    sign = 1.0 if side == "long" else -1.0
    signed = -sign * w["fundingRate"] * qty * prices
    if mode == "actual_signed":
        return float((signed * mult).sum())
    return float(signed.clip(upper=0.0).sum() * mult)

# ── deterministic fill RNG ───────────────────────────────────────────────────

def make_rng(seed_str):
    """Create a deterministic RNG from a seed string for reproducible fill simulation."""
    h = int(hashlib.sha256(seed_str.encode()).hexdigest()[:8], 16)  # 32-bit seed
    return np.random.RandomState(h)

# ── backtest engine ──────────────────────────────────────────────────────────

@dataclass
class BotConfig:
    name: str
    interval: str
    warmup_bars: int
    risk_pct: float
    stop_atr_mult: float
    trail_atr_mult: float
    max_hold_bars: int
    has_trailing: bool  # v40: False (stop only), v48b: True
    has_exit_signal: bool  # v40: True (ema cross), v48b: False

@dataclass
class ExecutionConfig:
    name: str
    mode: str  # "market" or "limit"
    maker_fee: float  # 0.0001 = 1.0 bps
    taker_fee: float  # 0.00045 = 4.5 bps
    market_entry_slip: float  # bps for market entry
    market_exit_slip: float  # bps for market exit
    stop_slip: float  # bps for stop execution
    fill_rate: float  # 0.0-1.0, probability limit fills
    fallback_slip: float  # bps for missed-limit fallback entry
    description: str

class BacktestEngine:
    def __init__(self, bot: BotConfig, exec_cfg: ExecutionConfig, funding, hp):
        self.bot = bot
        self.ec = exec_cfg
        self.funding = funding
        self.hp = hp
        self.rng = make_rng(f"{bot.name}_{exec_cfg.name}_fill") if exec_cfg.mode == "limit" else None

    def _fee(self, qty, price, fee_rate):
        return qty * price * fee_rate

    def _close_trade(self, pos, qty, entry, entry_time, signal_time, exit_time,
                     exit_px, fee_rate, funding_mode, funding_mult, trades, reason):
        side = "long" if pos == 1 else "short"
        fp = funding_pnl(self.funding, self.hp, entry_time, exit_time, side, qty, funding_mode, funding_mult)
        fees = self._fee(qty, entry, fee_rate) + self._fee(qty, exit_px, fee_rate)
        gp = qty * (exit_px - entry) if pos == 1 else qty * (entry - exit_px)
        pnl = gp - fees + fp
        trades.append({
            "signal_time": signal_time, "entry_time": entry_time,
            "exit_time": exit_time, "dir": side, "entry": entry, "exit": exit_px,
            "qty": qty, "gross_pnl": gp, "fees": -fees, "funding": fp,
            "pnl": pnl, "hold_hours": (exit_time - entry_time).total_seconds() / 3600,
            "reason": reason, "entry_type": "limit" if self.ec.mode == "limit" else "market"
        })
        return pnl, fp, fees

    def run(self, df, initial_capital=10_000.0):
        bot, ec = self.bot, self.ec
        pos, qty, entry, et, st = 0, 0.0, 0.0, None, None
        stop, trail, hw = np.nan, np.nan, np.nan
        bars_held = 0
        realized, trades, eq_rows = 0.0, [], []
        ftot, ffees = 0.0, 0.0
        limit_filled = 0
        limit_missed = 0

        for i in range(bot.warmup_bars, len(df) - 1):
            t, b, nb, nt = df.index[i], df.iloc[i], df.iloc[i+1], df.index[i+1]
            if any(np.isnan(b.get(c, np.nan)) for c in ["atr14"]):
                continue
            # v40 also needs ema columns
            if bot.name == "v40":
                if any(np.isnan(b.get(c, np.nan)) for c in ["ema50", "ema200", "ema200_daily"]):
                    continue

            exited = False
            atr = float(b["atr14"]) if not np.isnan(b["atr14"]) else 0

            # ── trailing stop update ──
            if bot.has_trailing:
                if pos == 1 and atr > 0:
                    hw = max(hw, float(b["high"]))
                    trail = hw - bot.trail_atr_mult * atr
                elif pos == -1 and atr > 0:
                    hw = min(hw, float(b["low"]))
                    trail = hw + bot.trail_atr_mult * atr

            def do_exit(reason):
                nonlocal pos, qty, entry, et, st, stop, trail, hw, bars_held, realized, ftot, ffees
                side = "long" if pos == 1 else "short"
                if reason in ("trail_stop", "stop"):
                    raw = min(float(trail if bot.has_trailing else stop), float(b["open"])) if pos == 1 else max(float(trail if bot.has_trailing else stop), float(b["open"]))
                    px = apply_slippage(raw, side, "exit", ec.stop_slip)
                    fr = ec.taker_fee  # stops always market
                elif reason == "max_hold":
                    px = apply_slippage(float(nb["open"]), side, "exit", ec.market_exit_slip)
                    fr = ec.taker_fee
                elif reason in ("ema_inverse_next_open", "ema_cross"):
                    px = apply_slippage(float(nb["open"]), side, "exit", ec.market_exit_slip)
                    fr = ec.taker_fee
                else:
                    px = apply_slippage(float(b["close"]), side, "exit", ec.market_exit_slip)
                    fr = ec.taker_fee

                pnl, fp, fees = self._close_trade(pos, qty, entry, et, st, t, px, fr, "actual_signed", 1.0, trades, reason)
                realized += pnl; ftot += fp; ffees += fees
                pos, qty, entry, et, st, stop, trail, hw, bars_held = 0, 0.0, 0.0, None, None, np.nan, np.nan, np.nan, 0

            # ── exit checks ──
            if bot.has_trailing:
                if pos == 1 and b["low"] <= trail:
                    do_exit("trail_stop"); exited = True
                elif pos == -1 and b["high"] >= trail:
                    do_exit("trail_stop"); exited = True

            if not exited and pos == 1 and b["low"] <= stop:
                do_exit("stop"); exited = True
            elif not exited and pos == -1 and b["high"] >= stop:
                do_exit("stop"); exited = True

            if not exited and pos != 0:
                bars_held += 1
                if bars_held >= bot.max_hold_bars:
                    do_exit("max_hold"); exited = True

            # v40: EMA cross exit
            if bot.has_exit_signal and not exited and pos != 0:
                if pos == 1 and bool(b.get("long_exit_signal", False)):
                    do_exit("ema_cross"); exited = True
                elif pos == -1 and bool(b.get("short_exit_signal", False)):
                    do_exit("ema_cross"); exited = True

            # ── entry ──
            if pos == 0:
                equity = initial_capital + realized
                risk = equity * bot.risk_pct

                if bool(b.get("long_signal", False)) and atr > 0:
                    if ec.mode == "market":
                        ep = apply_slippage(float(nb["open"]), "long", "entry", ec.market_entry_slip)
                        fee_rate = ec.taker_fee
                        entry_type = "market"
                    else:
                        # Limit order: placed at signal bar close
                        limit_price = float(b["close"])
                        fee_rate = ec.maker_fee
                        # Deterministic fill check
                        if self.rng.random() < ec.fill_rate:
                            ep = limit_price  # filled at limit
                            entry_type = "limit"
                            limit_filled += 1
                        else:
                            # Missed: fallback to next bar open with market
                            ep = apply_slippage(float(nb["open"]), "long", "entry", ec.fallback_slip)
                            fee_rate = ec.taker_fee
                            entry_type = "market_fallback"
                            limit_missed += 1

                    q = risk / (bot.stop_atr_mult * atr)
                    if q > 0:
                        pos, qty, entry, et, st = 1, float(q), ep, nt, t
                        stop = ep - bot.stop_atr_mult * atr
                        if bot.has_trailing:
                            trail = ep - bot.trail_atr_mult * atr
                            hw = float(ep)
                        bars_held = 0

                elif bool(b.get("short_signal", False)) and atr > 0:
                    if ec.mode == "market":
                        ep = apply_slippage(float(nb["open"]), "short", "entry", ec.market_entry_slip)
                        fee_rate = ec.taker_fee
                        entry_type = "market"
                    else:
                        limit_price = float(b["close"])
                        fee_rate = ec.maker_fee
                        if self.rng.random() < ec.fill_rate:
                            ep = limit_price
                            entry_type = "limit"
                            limit_filled += 1
                        else:
                            ep = apply_slippage(float(nb["open"]), "short", "entry", ec.fallback_slip)
                            fee_rate = ec.taker_fee
                            entry_type = "market_fallback"
                            limit_missed += 1

                    q = risk / (bot.stop_atr_mult * atr)
                    if q > 0:
                        pos, qty, entry, et, st = -1, float(q), ep, nt, t
                        stop = ep + bot.stop_atr_mult * atr
                        if bot.has_trailing:
                            trail = ep + bot.trail_atr_mult * atr
                            hw = float(ep)
                        bars_held = 0

            # ── equity tracking ──
            op = 0.0
            if pos == 1:
                op = qty * (b["close"] - entry)
            elif pos == -1:
                op = qty * (entry - b["close"])
            eq_rows.append({"time": t, "equity": initial_capital + realized + op})

        # ── close final position ──
        if pos != 0 and et is not None:
            t = df.index[-1]
            side = "long" if pos == 1 else "short"
            px = apply_slippage(float(df.iloc[-1]["close"]), side, "exit", ec.market_exit_slip)
            pnl, fp, fees = self._close_trade(pos, qty, entry, et, st, t, px, ec.taker_fee, "actual_signed", 1.0, trades, "eod")

        td = pd.DataFrame(trades)
        ed = pd.DataFrame(eq_rows).set_index("time") if eq_rows else pd.DataFrame(columns=["equity"])
        fill_stats = {"limit_filled": limit_filled, "limit_missed": limit_missed,
                       "total_signals": limit_filled + limit_missed,
                       "actual_fill_rate": round(limit_filled / max(limit_filled + limit_missed, 1), 4)}
        return td, ed, {"fees_total": round(ffees, 2), "funding_total": round(ftot, 2)}, fill_stats

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

# ── main ─────────────────────────────────────────────────────────────────────

def main():
    repo_root = Path(__file__).resolve().parents[1]
    out_dir = repo_root / "docs/backtests/limit_order_validation"
    out_dir.mkdir(parents=True, exist_ok=True)

    COIN = "BTC"

    # ── Bot configs ──
    bots = {
        "v40": BotConfig(
            name="v40", interval="4h", warmup_bars=220, risk_pct=0.02,
            stop_atr_mult=3.0, trail_atr_mult=3.0, max_hold_bars=999,
            has_trailing=False, has_exit_signal=True,
        ),
        "v48b": BotConfig(
            name="v48b", interval="1h", warmup_bars=200, risk_pct=0.015,
            stop_atr_mult=3.0, trail_atr_mult=3.0, max_hold_bars=24,
            has_trailing=True, has_exit_signal=False,
        ),
    }

    # ── Execution configs ──
    market_cfg = ExecutionConfig(
        name="market", mode="market",
        maker_fee=0.0001, taker_fee=0.00045,
        market_entry_slip=3.0, market_exit_slip=3.0, stop_slip=8.0,
        fill_rate=1.0, fallback_slip=0.0,
        description="Market orders, 4.5bps taker, 3bps entry slip"
    )

    limit_configs = {
        "optimistic": ExecutionConfig(
            name="limit_optimistic", mode="limit",
            maker_fee=0.0001, taker_fee=0.00045,
            market_entry_slip=3.0, market_exit_slip=3.0, stop_slip=8.0,
            fill_rate=0.80, fallback_slip=5.5,  # 4.5 + 1.0 slippage
            description="Limit orders, 80% fill, 1bps maker, fallback 5.5bps"
        ),
        "baseline": ExecutionConfig(
            name="limit_baseline", mode="limit",
            maker_fee=0.0001, taker_fee=0.00045,
            market_entry_slip=3.0, market_exit_slip=3.0, stop_slip=8.0,
            fill_rate=None, fallback_slip=5.5,
            description="Limit orders, baseline fill (70% v40 / 60% v48b), 1bps maker"
        ),
        "stressed": ExecutionConfig(
            name="limit_stressed", mode="limit",
            maker_fee=0.0001, taker_fee=0.00045,
            market_entry_slip=3.0, market_exit_slip=3.0, stop_slip=8.0,
            fill_rate=0.50, fallback_slip=5.5,
            description="Limit orders, 50% fill, 1bps maker, fallback 5.5bps"
        ),
    }

    # Set baseline fill rates per bot
    limit_configs["baseline_v40"] = ExecutionConfig(
        name="limit_baseline_v40", mode="limit",
        maker_fee=0.0001, taker_fee=0.00045,
        market_entry_slip=3.0, market_exit_slip=3.0, stop_slip=8.0,
        fill_rate=0.70, fallback_slip=5.5,
        description="Limit orders, 70% fill (v40 baseline), 1bps maker"
    )
    limit_configs["baseline_v48b"] = ExecutionConfig(
        name="limit_baseline_v48b", mode="limit",
        maker_fee=0.0001, taker_fee=0.00045,
        market_entry_slip=3.0, market_exit_slip=3.0, stop_slip=8.0,
        fill_rate=0.60, fallback_slip=5.5,
        description="Limit orders, 60% fill (v48b baseline), 1bps maker"
    )

    # ── Fetch data ──
    print("=" * 70)
    print("  Limit Order Backtest — v40 (4h) vs v48b (1h)")
    print("=" * 70)

    results = {}

    for bot_name, bot in bots.items():
        print(f"\n{'='*50}")
        print(f"  {bot_name}: {bot.interval} BTC")
        print(f"{'='*50}")

        end_ms = now_floor_ms(bot.interval) - 1
        earliest = infer_earliest(COIN, bot.interval)
        if not earliest:
            raise RuntimeError(f"No {bot.interval} data for {COIN}")

        print(f"  Fetching {bot.interval} klines...")
        klines = fetch_klines(COIN, bot.interval, int(earliest.timestamp()*1000), end_ms)
        freq = freq_for(bot.interval)
        rs, re, _ = longest_continuous_segment(klines, freq)
        strict = klines.loc[(klines.index >= rs) & (klines.index <= re)].copy()
        print(f"  Window: {rs.date()} → {re.date()} ({(re-rs).days}d, {len(strict)} bars)")

        # Fetch funding
        print(f"  Fetching funding...")
        funding = fetch_funding(COIN, int(rs.timestamp()*1000), int(re.timestamp()*1000))
        hp = strict["close"].reindex(funding.index, method="ffill")

        # Build features
        print(f"  Building features...")
        if bot_name == "v40":
            daily = fetch_klines(COIN, "1d", dt_to_ms("2021-01-01"), end_ms)
            feat = build_v40_features(strict, daily)
        else:
            feat = build_v48b_features(strict)

        weeks = (re - rs).days / 7
        ls = int(feat.iloc[bot.warmup_bars:]["long_signal"].sum())
        ss = int(feat.iloc[bot.warmup_bars:]["short_signal"].sum())
        print(f"  Signals: {ls+ss} (L:{ls} S:{ss}) | ~{(ls+ss)/max(weeks,1):.1f}/week")

        bpy = bars_per_year(bot.interval)
        bot_results = {}

        # ── Market order baseline ──
        print(f"\n  ▶ Market orders...")
        eng = BacktestEngine(bot, market_cfg, funding, hp)
        td, ed, costs, fill_stats = eng.run(feat)
        m = calc_metrics(td, ed, 10_000.0, bpy)
        print(f"    Return: {m['return_pct']:+.2f}% | CAGR: {m['cagr']:.1f}% | PF: {m['pf']:.2f} | DD: {m['max_dd']:.1f}% | T: {m['trades']} | Fees: ${costs['fees_total']:.2f}")
        bot_results["market"] = {"metrics": m, "costs": costs, "fill_stats": fill_stats}
        if not td.empty:
            td.to_csv(out_dir / f"{bot_name}_market_trades.csv", index=False)

        # ── Limit order scenarios ──
        if bot_name == "v40":
            limit_scenarios = [
                ("optimistic", limit_configs["optimistic"]),
                ("baseline", limit_configs["baseline_v40"]),
                ("stressed", limit_configs["stressed"]),
            ]
        else:
            limit_scenarios = [
                ("optimistic", limit_configs["optimistic"]),
                ("baseline", limit_configs["baseline_v48b"]),
                ("stressed", limit_configs["stressed"]),
            ]

        for label, ec in limit_scenarios:
            print(f"  ▶ Limit {label} (fill={ec.fill_rate:.0%})...")
            eng = BacktestEngine(bot, ec, funding, hp)
            td, ed, costs, fill_stats = eng.run(feat)
            m = calc_metrics(td, ed, 10_000.0, bpy)
            print(f"    Return: {m['return_pct']:+.2f}% | CAGR: {m['cagr']:.1f}% | PF: {m['pf']:.2f} | DD: {m['max_dd']:.1f}% | T: {m['trades']} | Fees: ${costs['fees_total']:.2f}")
            print(f"    Filled: {fill_stats['limit_filled']} | Missed: {fill_stats['limit_missed']} | Actual fill: {fill_stats['actual_fill_rate']:.1%}")
            bot_results[f"limit_{label}"] = {"metrics": m, "costs": costs, "fill_stats": fill_stats}
            if not td.empty:
                td.to_csv(out_dir / f"{bot_name}_limit_{label}_trades.csv", index=False)

        results[bot_name] = {
            "window": {"start": str(rs.date()), "end": str(re.date()), "days": (re-rs).days, "bars": len(strict)},
            "signals": {"long": ls, "short": ss, "total": ls+ss, "per_week": round((ls+ss)/max(weeks,1), 2)},
            "results": bot_results,
        }

    # ── Comparison table ──
    print("\n" + "=" * 70)
    print("  COMPARISON TABLE")
    print("=" * 70)

    # Build comparison: v40 market | v40 limit_baseline | v48b market | v48b limit_baseline
    v40_mkt = results["v40"]["results"]["market"]["metrics"]
    v40_lim = results["v40"]["results"]["limit_baseline"]["metrics"]
    v48b_mkt = results["v48b"]["results"]["market"]["metrics"]
    v48b_lim = results["v48b"]["results"]["limit_baseline"]["metrics"]

    v40_mkt_costs = results["v40"]["results"]["market"]["costs"]
    v40_lim_costs = results["v40"]["results"]["limit_baseline"]["costs"]
    v48b_mkt_costs = results["v48b"]["results"]["market"]["costs"]
    v48b_lim_costs = results["v48b"]["results"]["limit_baseline"]["costs"]

    v40_fill = results["v40"]["results"]["limit_baseline"]["fill_stats"]
    v48b_fill = results["v48b"]["results"]["limit_baseline"]["fill_stats"]

    print(f"\n  {'Metric':<16} {'v40 market':>12} {'v40 limit(70%)':>14} {'v48b market':>12} {'v48b limit(60%)':>15}")
    print(f"  {'-'*72}")
    for m in ["return_pct", "cagr", "max_dd", "pf", "wr", "trades", "sharpe", "sortino"]:
        print(f"  {m:<16} {v40_mkt[m]:>12} {v40_lim[m]:>14} {v48b_mkt[m]:>12} {v48b_lim[m]:>15}")

    print(f"\n  {'Fees ($)':<16} {v40_mkt_costs['fees_total']:>12.2f} {v40_lim_costs['fees_total']:>14.2f} {v48b_mkt_costs['fees_total']:>12.2f} {v48b_lim_costs['fees_total']:>15.2f}")
    print(f"  {'Fee savings':<16} {'':>12} ${v40_mkt_costs['fees_total'] - v40_lim_costs['fees_total']:>13.2f} {'':>12} ${v48b_mkt_costs['fees_total'] - v48b_lim_costs['fees_total']:>14.2f}")

    print(f"\n  Limit fill stats:")
    print(f"    v40:  filled={v40_fill['limit_filled']}, missed={v40_fill['limit_missed']}, actual_rate={v40_fill['actual_fill_rate']:.1%}")
    print(f"    v48b: filled={v48b_fill['limit_filled']}, missed={v48b_fill['limit_missed']}, actual_rate={v48b_fill['actual_fill_rate']:.1%}")

    # ── All scenarios ──
    print(f"\n{'='*70}")
    print("  ALL SCENARIOS")
    print(f"{'='*70}")

    for bot_name in ["v40", "v48b"]:
        print(f"\n  {bot_name}:")
        mkt = results[bot_name]["results"]["market"]["metrics"]
        print(f"    Market:     CAGR {mkt['cagr']:>7.1f}%  PF {mkt['pf']:>5.2f}  DD {mkt['max_dd']:>6.1f}%  T {mkt['trades']:>3}")
        for label in ["optimistic", "baseline", "stressed"]:
            key = f"limit_{label}"
            if key in results[bot_name]["results"]:
                r = results[bot_name]["results"][key]
                m = r["metrics"]
                fs = r["fill_stats"]
                delta_cagr = m["cagr"] - mkt["cagr"]
                delta_pf = m["pf"] - mkt["pf"]
                print(f"    Limit {label:>10}: CAGR {m['cagr']:>7.1f}% ({delta_cagr:+.1f})  PF {m['pf']:>5.2f} ({delta_pf:+.2f})  DD {m['max_dd']:>6.1f}%  T {m['trades']:>3}  fill={fs['actual_fill_rate']:.0%}")

    # ── Verdict ──
    print(f"\n{'='*70}")
    print("  VERDICT")
    print(f"{'='*70}")

    for bot_name in ["v40", "v48b"]:
        mkt = results[bot_name]["results"]["market"]["metrics"]
        lim = results[bot_name]["results"]["limit_baseline"]["metrics"]
        mkt_fees = results[bot_name]["results"]["market"]["costs"]["fees_total"]
        lim_fees = results[bot_name]["results"]["limit_baseline"]["costs"]["fees_total"]
        fs = results[bot_name]["results"]["limit_baseline"]["fill_stats"]

        cagr_delta = lim["cagr"] - mkt["cagr"]
        fee_savings = mkt_fees - lim_fees
        trades_delta = lim["trades"] - mkt["trades"]

        if cagr_delta > 0:
            verdict = "LIMIT ORDERS HELP — better CAGR with fee savings"
        elif abs(cagr_delta) < 1.0:
            verdict = "NEUTRAL — marginal CAGR impact, fee savings offset by missed trades"
        else:
            verdict = "LIMIT ORDERS HURT — too many missed trades degrade performance"

        print(f"\n  {bot_name}:")
        print(f"    CAGR delta: {cagr_delta:+.1f}%  |  Fee savings: ${fee_savings:.2f}  |  Trades delta: {trades_delta:+d}")
        print(f"    Fill rate: {fs['actual_fill_rate']:.0%} ({fs['limit_filled']}/{fs['total_signals']})")
        print(f"    → {verdict}")

    # ── Save results ──
    summary = {
        "generated_at": pd.Timestamp.now(tz="UTC").isoformat(),
        "methodology": {
            "limit_model": "Limit at signal bar close, probabilistic fill, fallback to market next bar",
            "maker_fee": "1.0 bps", "taker_fee": "4.5 bps",
            "fallback_slippage": "5.5 bps (4.5 taker + 1.0 slip)",
            "fill_rates": {"optimistic": "80%", "v40_baseline": "70%", "v48b_baseline": "60%", "stressed": "50%"},
            "exits": "Always market (stops/trails cannot be limit orders)",
        },
        "bots": results,
    }
    (out_dir / "summary.json").write_text(json.dumps(_native(summary), indent=2))

    # Write comparison CSV
    comp_rows = []
    for m in ["return_pct", "cagr", "max_dd", "pf", "wr", "trades", "sharpe", "sortino"]:
        comp_rows.append({
            "metric": m,
            "v40_market": v40_mkt[m],
            "v40_limit_baseline": v40_lim[m],
            "v48b_market": v48b_mkt[m],
            "v48b_limit_baseline": v48b_lim[m],
        })
    comp_rows.append({
        "metric": "fees_total",
        "v40_market": v40_mkt_costs["fees_total"],
        "v40_limit_baseline": v40_lim_costs["fees_total"],
        "v48b_market": v48b_mkt_costs["fees_total"],
        "v48b_limit_baseline": v48b_lim_costs["fees_total"],
    })
    pd.DataFrame(comp_rows).to_csv(out_dir / "comparison.csv", index=False)

    print(f"\n  Output → {out_dir}")
    print(f"  Files: summary.json, comparison.csv, *_trades.csv")

if __name__ == "__main__":
    main()
