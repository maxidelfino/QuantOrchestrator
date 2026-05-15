#!/usr/bin/env python3
"""v51 Position Sizing Optimization — find optimal risk level for 23%+ CAGR / MaxDD > -10%

Baseline: v48a entry logic + max_hold=16 bars
  Entry: ADX(14)>20, RSI(14) 35-50 long / 50-65 short, RSI momentum, candle dir, DI confirm
  Exit: 3x ATR trailing stop, max hold 16 bars
  Current: 1.5% risk → CAGR 19.5%, PF 2.31, MaxDD -3.5%, 30 trades

Tests:
  Risk levels: 1.0%, 1.5%, 2.0%, 2.5%, 3.0%, 4.0%
  Sizing methods: fixed_fractional, fixed_dollar, kelly_fraction

For each: CAGR, MaxDD, PF, Sharpe, worst month, largest single loss, max consecutive losses
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


# ── feature builder (v48a entry logic) ───────────────────────────────────────

def build_features(df):
    """v48a entry: ADX>20, RSI 35-50 long / 50-65 short, candle + DI + momentum."""
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


# ── sizing methods ───────────────────────────────────────────────────────────

def calc_kelly_from_trades(trades_df):
    """Calculate Kelly fraction from completed trades.
    
    Kelly = W - (1-W)/R
    where W = win rate, R = avg_win / avg_loss (absolute)
    Returns (kelly_fraction, win_rate, avg_win, avg_loss, num_trades)
    """
    if trades_df.empty or len(trades_df) < 5:
        return 0.0, 0.0, 0.0, 0.0, 0
    
    pnl = trades_df["pnl"]
    wins = pnl[pnl > 0]
    losses = pnl[pnl <= 0]
    
    w = len(wins) / len(pnl)
    avg_win = wins.mean() if len(wins) > 0 else 0
    avg_loss = abs(losses.mean()) if len(losses) > 0 else 0
    
    if avg_loss == 0 or w == 0:
        return 0.0, w, avg_win, avg_loss, len(pnl)
    
    r = avg_win / avg_loss
    kelly = w - (1 - w) / r
    
    return max(kelly, 0.0), w, avg_win, avg_loss, len(pnl)


# ── engine with sizing method support ────────────────────────────────────────

@dataclass
class SizingConfig:
    name: str
    risk_pct: float            # for fixed_fractional
    risk_dollar: float         # for fixed_dollar ($ amount at risk)
    sizing_method: str         # "fixed_fractional", "fixed_dollar", "kelly_fraction"
    kelly_fraction: float = 0  # pre-computed Kelly (used only if sizing_method == "kelly_fraction")
    fee_rate: float = 0.00045
    entry_slippage_bps: float = 3.0
    exit_slippage_bps: float = 3.0
    stop_slippage_bps: float = 8.0
    funding_mode: str = "actual_signed"
    funding_multiplier: float = 1.0
    initial_capital: float = 10_000.0
    stop_atr_mult: float = 3.0
    trail_atr_mult: float = 3.0
    max_hold_bars: int = 16
    warmup_bars: int = 200
    description: str = ""


class Engine:
    def __init__(self, sc, funding, hp):
        self.sc, self.funding, self.hp = sc, funding, hp

    def _compute_risk(self, equity, atr):
        """Compute dollar risk based on sizing method."""
        sc = self.sc
        if sc.sizing_method == "fixed_fractional":
            return equity * sc.risk_pct
        elif sc.sizing_method == "fixed_dollar":
            return sc.risk_dollar
        elif sc.sizing_method == "kelly_fraction":
            # Kelly fraction of equity
            return equity * sc.kelly_fraction
        return equity * sc.risk_pct  # fallback

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

            # Check exits
            if pos == 1 and not np.isnan(trail) and b["low"] <= trail:
                do_exit("trail_stop"); exited = True
            elif pos == -1 and not np.isnan(trail) and b["high"] >= trail:
                do_exit("trail_stop"); exited = True
            if not exited and pos == 1 and not np.isnan(stop) and b["low"] <= stop:
                do_exit("stop"); exited = True
            elif not exited and pos == -1 and not np.isnan(stop) and b["high"] >= stop:
                do_exit("stop"); exited = True
            if not exited and pos != 0:
                bars += 1
                if bars >= sc.max_hold_bars:
                    do_exit("max_hold"); exited = True

            # Entry
            if pos == 0:
                equity = sc.initial_capital + realized
                risk_dollars = self._compute_risk(equity, atr)
                if bool(b.get("long_signal")) and atr > 0 and risk_dollars > 0:
                    ep = apply_slippage(float(nb["open"]), "long", "entry", sc.entry_slippage_bps)
                    q = risk_dollars / (sc.stop_atr_mult * atr)
                    if q > 0:
                        pos, qty, entry, et, st = 1, float(q), ep, nt, t
                        stop = ep - sc.stop_atr_mult * atr
                        trail = ep - sc.trail_atr_mult * atr
                        hw = float(ep); bars = 0
                elif bool(b.get("short_signal")) and atr > 0 and risk_dollars > 0:
                    ep = apply_slippage(float(nb["open"]), "short", "entry", sc.entry_slippage_bps)
                    q = risk_dollars / (sc.stop_atr_mult * atr)
                    if q > 0:
                        pos, qty, entry, et, st = -1, float(q), ep, nt, t
                        stop = ep + sc.stop_atr_mult * atr
                        trail = ep + sc.trail_atr_mult * atr
                        hw = float(ep); bars = 0

            # Equity tracking
            op = qty * (b["close"] - entry) if pos == 1 else (qty * (entry - b["close"]) if pos == -1 else 0)
            eq_rows.append({"time": t, "equity": sc.initial_capital + realized + op})

        # Close remaining position
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


def calc_trade_details(trades):
    """Additional trade-level risk metrics."""
    if trades.empty:
        return {"worst_single_loss": 0, "worst_month_pct": 0, "max_consec_losses": 0}
    
    pnl = trades["pnl"]
    worst_loss = float(pnl.min())
    
    # Worst month: aggregate PnL by calendar month
    trades_with_month = trades.copy()
    trades_with_month["month"] = trades_with_month["exit_time"].dt.to_period("M")
    monthly = trades_with_month.groupby("month")["pnl"].sum()
    # Convert to % of starting capital ($10k)
    worst_month_pct = float(monthly.min() / 10_000 * 100) if len(monthly) > 0 else 0
    
    # Max consecutive losses
    max_consec = 0
    current_consec = 0
    for p in pnl:
        if p <= 0:
            current_consec += 1
            max_consec = max(max_consec, current_consec)
        else:
            current_consec = 0
    
    return {"worst_single_loss": round(worst_loss, 2), "worst_month_pct": round(worst_month_pct, 2), "max_consec_losses": max_consec}


def _native(o):
    if isinstance(o, (np.integer,)): return int(o)
    if isinstance(o, (np.floating,)): return float(o)
    if isinstance(o, dict): return {k: _native(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)): return [_native(v) for v in o]
    return o


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    repo_root = Path(__file__).resolve().parents[1]
    out_dir = repo_root / "docs/backtests/v51_sizing_optimization"
    out_dir.mkdir(parents=True, exist_ok=True)

    COIN = "BTC"
    INTERVAL = "1h"
    bpy = bars_per_year_for(INTERVAL)
    freq = freq_for(INTERVAL)

    # ── Fetch data once ──
    print("Fetching 1h data...")
    end_ms = now_floor_ms(INTERVAL) - 1
    earliest = infer_earliest(COIN, INTERVAL)
    if not earliest:
        raise RuntimeError(f"No {INTERVAL} data for {COIN}")
    klines = fetch_klines(COIN, INTERVAL, int(earliest.timestamp()*1000), end_ms)
    rs, re, _ = longest_continuous_segment(klines, freq)
    strict = klines.loc[(klines.index >= rs) & (klines.index <= re)].copy()
    print(f"Window: {rs.date()} → {re.date()} ({(re-rs).days}d, {len(strict)} bars)")

    funding = fetch_funding(COIN, int(rs.timestamp()*1000), int(re.timestamp()*1000))
    hp = strict["close"].reindex(funding.index, method="ffill")
    
    feat = build_features(strict)
    weeks = (re - rs).days / 7
    ls = int(feat.iloc[200:]["long_signal"].sum())
    ss = int(feat.iloc[200:]["short_signal"].sum())
    print(f"Signals: {ls+ss} (L:{ls} S:{ss}) | ~{(ls+ss)/max(weeks,1):.1f}/week")

    # ── Step 1: Run baseline (1.5% fixed fractional) to compute Kelly ──
    print("\n── Step 1: Baseline run (1.5% fixed fractional) for Kelly calculation ──")
    baseline_sc = SizingConfig(
        name="baseline_1.5pct", risk_pct=0.015, risk_dollar=0,
        sizing_method="fixed_fractional"
    )
    baseline_td, baseline_ed, baseline_costs = Engine(baseline_sc, funding, hp).run(feat, COIN)
    baseline_m = calc_metrics(baseline_td, baseline_ed, baseline_sc.initial_capital, bpy)
    baseline_d = calc_trade_details(baseline_td)
    
    kelly, wr, avg_win, avg_loss, n_trades = calc_kelly_from_trades(baseline_td)
    half_kelly = kelly * 0.5
    quarter_kelly = kelly * 0.25
    
    print(f"  Baseline: CAGR {baseline_m['cagr']:.1f}%  PF {baseline_m['pf']:.2f}  DD {baseline_m['max_dd']:.1f}%  T {baseline_m['trades']}")
    print(f"  Win rate: {wr:.1f}%  Avg win: ${avg_win:.2f}  Avg loss: ${avg_loss:.2f}")
    print(f"  Full Kelly: {kelly:.4f} ({kelly*100:.2f}%)")
    print(f"  Half Kelly: {half_kelly:.4f} ({half_kelly*100:.2f}%)")
    print(f"  Quarter Kelly: {quarter_kelly:.4f} ({quarter_kelly*100:.2f}%)")

    # ── Step 2: Define all test configs ──
    print("\n── Step 2: Running all sizing variants ──")
    
    # Risk levels for fixed fractional
    risk_levels = [0.010, 0.015, 0.020, 0.025, 0.030, 0.040]
    
    configs = []
    
    # Fixed fractional at each risk level
    for rp in risk_levels:
        label = f"ff_{int(rp*1000)}"  # ff_10, ff_15, ff_20, ...
        configs.append(SizingConfig(
            name=label, risk_pct=rp, risk_dollar=0,
            sizing_method="fixed_fractional",
            description=f"Fixed fractional {rp*100:.1f}%"
        ))
    
    # Fixed dollar: equivalent to 1.5% of $10k = $150, then scale
    # $150 risk = 1.5% baseline. Test proportional levels.
    dollar_levels = [100, 150, 200, 250, 300, 400]
    for dl in dollar_levels:
        label = f"fd_{dl}"
        configs.append(SizingConfig(
            name=label, risk_pct=0, risk_dollar=float(dl),
            sizing_method="fixed_dollar",
            description=f"Fixed dollar ${dl}"
        ))
    
    # Kelly variants
    if kelly > 0:
        configs.append(SizingConfig(
            name="kelly_full", risk_pct=0, risk_dollar=0,
            sizing_method="kelly_fraction", kelly_fraction=kelly,
            description=f"Full Kelly ({kelly*100:.2f}%)"
        ))
        configs.append(SizingConfig(
            name="kelly_half", risk_pct=0, risk_dollar=0,
            sizing_method="kelly_fraction", kelly_fraction=half_kelly,
            description=f"Half Kelly ({half_kelly*100:.2f}%)"
        ))
        configs.append(SizingConfig(
            name="kelly_quarter", risk_pct=0, risk_dollar=0,
            sizing_method="kelly_fraction", kelly_fraction=quarter_kelly,
            description=f"Quarter Kelly ({quarter_kelly*100:.2f}%)"
        ))

    # ── Run all configs ──
    results = []
    for sc in configs:
        print(f"  ▶ {sc.name} ({sc.description})...", end=" ", flush=True)
        td, ed, costs = Engine(sc, funding, hp).run(feat, COIN)
        m = calc_metrics(td, ed, sc.initial_capital, bpy)
        d = calc_trade_details(td)
        row = {"name": sc.name, "description": sc.description, **m, **costs, **d,
               "trades_per_week": round(m["trades"] / max(weeks, 1), 2)}
        results.append(row)
        print(f"CAGR {m['cagr']:.1f}%  DD {m['max_dd']:.1f}%  PF {m['pf']:.2f}  T {m['trades']}")
        
        if not td.empty:
            td.to_csv(out_dir / f"v51_{sc.name}_trades.csv", index=False)
        if not ed.empty:
            ed.to_csv(out_dir / f"v51_{sc.name}_equity.csv")

    # ── Results table ──
    print("\n" + "=" * 120)
    print("  V51 SIZING OPTIMIZATION — RESULTS")
    print("=" * 120)
    
    # Fixed fractional table
    print(f"\n  FIXED FRACTIONAL:")
    print(f"  {'Risk%':>6} | {'CAGR':>7} | {'MaxDD':>7} | {'PF':>5} | {'Sharpe':>7} | {'WorstLoss':>10} | {'MaxConsec':>10} | {'T':>4} | {'T/Wk':>5}")
    print(f"  {'-'*80}")
    for r in results:
        if r["name"].startswith("ff_"):
            rpct = r["name"].replace("ff_", "")
            rpct = f"{int(rpct)/10:.1f}%"
            print(f"  {rpct:>6} | {r['cagr']:>6.1f}% | {r['max_dd']:>6.1f}% | {r['pf']:>5.2f} | {r['sharpe']:>7.3f} | ${r['worst_single_loss']:>9.2f} | {r['max_consec_losses']:>10} | {r['trades']:>4} | {r['trades_per_week']:>5.2f}")

    # Fixed dollar table
    print(f"\n  FIXED DOLLAR:")
    print(f"  {'Risk$':>7} | {'CAGR':>7} | {'MaxDD':>7} | {'PF':>5} | {'Sharpe':>7} | {'WorstLoss':>10} | {'MaxConsec':>10} | {'T':>4} | {'T/Wk':>5}")
    print(f"  {'-'*80}")
    for r in results:
        if r["name"].startswith("fd_"):
            dval = r["name"].replace("fd_", "")
            print(f"  ${dval:>6} | {r['cagr']:>6.1f}% | {r['max_dd']:>6.1f}% | {r['pf']:>5.2f} | {r['sharpe']:>7.3f} | ${r['worst_single_loss']:>9.2f} | {r['max_consec_losses']:>10} | {r['trades']:>4} | {r['trades_per_week']:>5.2f}")

    # Kelly table
    print(f"\n  KELLY FRACTION:")
    print(f"  {'Variant':>12} | {'Kelly%':>7} | {'CAGR':>7} | {'MaxDD':>7} | {'PF':>5} | {'Sharpe':>7} | {'WorstLoss':>10} | {'MaxConsec':>10} | {'T':>4}")
    print(f"  {'-'*85}")
    for r in results:
        if r["name"].startswith("kelly_"):
            kf = r["name"].replace("kelly_", "")
            kelly_pct = r.get("kelly_fraction", 0) * 100 if "kelly_fraction" in r else 0
            print(f"  {kf:>12} | {kelly_pct:>6.2f}% | {r['cagr']:>6.1f}% | {r['max_dd']:>6.1f}% | {r['pf']:>5.2f} | {r['sharpe']:>7.3f} | ${r['worst_single_loss']:>9.2f} | {r['max_consec_losses']:>10} | {r['trades']:>4}")

    # ── Verdict ──
    print(f"\n  VERDICT (target: CAGR>=23%, MaxDD>-10%, PF>1.5):")
    print(f"  {'-'*90}")
    
    # Which reach 23% CAGR?
    reach_23 = [r for r in results if r["cagr"] >= 23 and r["max_dd"] > -10 and r["pf"] > 1.5]
    
    if reach_23:
        print(f"\n  Variants reaching 23%+ CAGR with MaxDD>-10% and PF>1.5:")
        for r in sorted(reach_23, key=lambda x: x["cagr"], reverse=True):
            print(f"    {r['name']:15s} → CAGR {r['cagr']:.1f}%  DD {r['max_dd']:.1f}%  PF {r['pf']:.2f}")
    else:
        # Show closest
        best_constrained = None
        best_cagr_constrained = -999
        for r in results:
            if r["max_dd"] > -10 and r["pf"] > 1.5 and r["cagr"] > best_cagr_constrained:
                best_constrained = r
                best_cagr_constrained = r["cagr"]
        
        print(f"\n  NO variant reaches 23% CAGR with MaxDD>-10% and PF>1.5")
        if best_constrained:
            print(f"  Best constrained: {best_constrained['name']} → CAGR {best_constrained['cagr']:.1f}%  DD {best_constrained['max_dd']:.1f}%  PF {best_constrained['pf']:.2f}")
            print(f"  Gap to 23%: {23 - best_constrained['cagr']:.1f}pp")
        
        # Show highest CAGR regardless of DD
        best_any = max(results, key=lambda x: x["cagr"])
        print(f"  Highest CAGR (any DD): {best_any['name']} → CAGR {best_any['cagr']:.1f}%  DD {best_any['max_dd']:.1f}%")

    # ── Recommendation ──
    print(f"\n  RECOMMENDATION:")
    print(f"  {'-'*90}")
    
    # Find best risk-adjusted: highest CAGR with DD > -10%
    safe = [r for r in results if r["max_dd"] > -10 and r["pf"] > 1.5]
    if safe:
        best_safe = max(safe, key=lambda x: x["cagr"])
        print(f"  Best safe option: {best_safe['name']} ({best_safe['description']})")
        print(f"    CAGR {best_safe['cagr']:.1f}% | MaxDD {best_safe['max_dd']:.1f}% | PF {best_safe['pf']:.2f} | Sharpe {best_safe['sharpe']:.3f}")
        print(f"    Worst single loss: ${best_safe['worst_single_loss']:.2f} | Max consec losses: {best_safe['max_consec_losses']}")
    
    # Kelly assessment
    print(f"\n  Kelly assessment:")
    print(f"    Full Kelly ({kelly*100:.2f}%): {'USABLE' if kelly > 0 else 'NOT USABLE'}")
    if kelly > 0:
        if kelly > 0.10:
            print(f"    WARNING: Kelly > 10% — very aggressive for only {n_trades} trades (sample too small)")
            print(f"    RECOMMEND: Use half Kelly ({half_kelly*100:.2f}%) or less")
        else:
            print(f"    Kelly is moderate — half Kelly ({half_kelly*100:.2f}%) is prudent")
    
    # Risk warning for high levels
    print(f"\n  Risk warnings:")
    for r in results:
        if r["max_dd"] <= -10:
            print(f"    {r['name']:15s} → MaxDD {r['max_dd']:.1f}% EXCEEDS -10% limit — DO NOT USE")

    # ── Save summary ──
    summary = {
        "baseline": {**baseline_m, **baseline_d, **baseline_costs,
                     "win_rate": round(wr, 2), "avg_win": round(avg_win, 2),
                     "avg_loss": round(avg_loss, 2), "n_trades": n_trades},
        "kelly": {"full": round(kelly, 6), "half": round(half_kelly, 6), "quarter": round(quarter_kelly, 6)},
        "results": [_native(r) for r in results],
        "target": {"cagr_min": 23, "max_dd_max": -10, "pf_min": 1.5},
    }
    (out_dir / "summary.json").write_text(json.dumps(_native(summary), indent=2))
    print(f"\n  → {out_dir}")
    print(f"  → summary.json saved")


if __name__ == "__main__":
    main()
