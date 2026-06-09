#!/usr/bin/env python3
"""v48 Final Validation — Post-restructure backtest using ACTUAL strategy module

This script imports BTCMomentum1hStrategy from the restructured monorepo
and runs it against Hyperliquid 1h data to verify performance is unchanged.

Parameters (exact per request):
  - 1h timeframe
  - ADX > 20
  - RSI 35-50 long / 50-65 short
  - RSI momentum (rising/falling vs 2 bars ago)
  - Candle direction confirms
  - +DI > -DI for longs
  - Trailing stop 3x ATR
  - Max hold 16 bars
  - Risk: 2.0% per trade
  - Next-bar execution
  - HL taker fees 4.5bps, hourly funding

Scenarios:
  - optimistic: 2bps entry/exit slippage, no funding cost
  - baseline:  3bps entry, 3bps exit, 8bps stop, actual signed funding
  - stressed:  5bps entry, 5bps exit, 12bps stop, funding x2 worst-case
"""

from __future__ import annotations

import json, math, sys, time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

# Add repo root to path so we can import the actual strategy
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from exchanges.hyperliquid.bots.btc_momentum_1h.strategy import BTCMomentum1hStrategy
from shared.python.strategy import Bar, Position, Signal

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
    else:
        floored = now.floor(interval)
    return int(floored.timestamp() * 1000)

def bars_per_year_for(interval: str) -> int:
    return {"1h": 8760, "2h": 4380, "4h": 2190, "1d": 365}.get(interval, 8760)

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

# ── scenario config ──────────────────────────────────────────────────────────

@dataclass
class ScenarioConfig:
    name: str
    fee_rate: float          # per side (taker = 0.00045 on HL)
    entry_slippage_bps: float
    exit_slippage_bps: float
    stop_slippage_bps: float
    funding_mode: str        # "none", "actual_signed", "stressed"
    funding_multiplier: float
    description: str
    initial_capital: float = 10_000.0

# ── cost model ───────────────────────────────────────────────────────────────

def apply_slippage(price, side, action, bps):
    bump = bps / 10_000.0
    if action == "entry":
        return price * (1 + bump) if side == "long" else price * (1 - bump)
    return price * (1 - bump) if side == "long" else price * (1 + bump)

def funding_pnl(funding, hourly_prices, entry_time, exit_time, side, qty, mode, mult):
    if funding.empty or mode == "none":
        return 0.0
    w = funding.loc[(funding.index > entry_time) & (funding.index <= exit_time)]
    if w.empty:
        return 0.0
    prices = hourly_prices.reindex(w.index, method="ffill")
    sign = 1.0 if side == "long" else -1.0
    signed = -sign * w["fundingRate"] * qty * prices
    if mode == "actual_signed":
        return float((signed * mult).sum())
    if mode == "stressed":
        # Worst case: assume funding always works against us, magnified
        return float(signed.clip(upper=0.0).sum() * mult)
    return 0.0

# ── engine using ACTUAL strategy class ───────────────────────────────────────

class StrategyEngine:
    def __init__(self, sc: ScenarioConfig, funding: pd.DataFrame, hp: pd.Series):
        self.sc = sc
        self.funding = funding
        self.hp = hp

    def run(self, df: pd.DataFrame, coin="BTC") -> tuple[pd.DataFrame, pd.DataFrame, dict]:
        sc = self.sc
        strategy = BTCMomentum1hStrategy(
            rsi_period=14,
            adx_period=14,
            adx_threshold=20.0,
            rsi_long_min=35.0,
            rsi_long_max=50.0,
            rsi_short_min=50.0,
            rsi_short_max=65.0,
            atr_period=14,
            stop_atr_mult=3.0,
            max_hold_bars=16,
            risk_pct=0.02,
        )

        # Compute indicators on full dataframe
        df_ind = strategy.compute_indicators(df, pd.DataFrame())

        # Warmup: need enough bars for all EWMs (14*3 is plenty)
        warmup = 50

        pos = None          # Optional[Position]
        qty = 0.0
        entry_price = 0.0
        entry_time = None
        signal_time = None
        stop_price = np.nan
        trail_price = np.nan
        highest = np.nan
        bars_held = 0
        realized = 0.0
        trades = []
        eq_rows = []
        ftot = 0.0
        ffees = 0.0

        for i in range(warmup, len(df) - 1):
            t = df.index[i]
            b = df_ind.iloc[i]
            nb = df_ind.iloc[i + 1]
            nt = df.index[i + 1]

            # Skip if indicators not ready
            if any(np.isnan(b.get(c, np.nan)) for c in ["atr", "adx", "rsi", "plus_di", "minus_di"]):
                continue

            atr = float(b["atr"]) if not np.isnan(b["atr"]) else 0.0
            if atr <= 0:
                continue

            # Build Bar object for strategy
            bar = Bar(
                time=t,
                open=float(b["open"]),
                high=float(b["high"]),
                low=float(b["low"]),
                close=float(b["close"]),
                volume=float(b["volume"]),
                indicators={
                    "atr": atr,
                    "adx": float(b["adx"]),
                    "rsi": float(b["rsi"]),
                    "plus_di": float(b["plus_di"]),
                    "minus_di": float(b["minus_di"]),
                }
            )

            # Update RSI state for momentum comparison
            strategy.update_rsi_state(float(b["rsi"]))

            exited = False

            # Update trailing stop
            if pos is not None:
                if pos.side == "long":
                    highest = max(highest, float(b["high"]))
                    trail_price = highest - 3.0 * atr
                else:
                    lowest = min(highest, float(b["low"]))  # reuse var name for short
                    trail_price = lowest + 3.0 * atr

            def do_exit(reason, exit_price_raw):
                nonlocal pos, qty, entry_price, entry_time, signal_time, stop_price, trail_price, highest, bars_held, realized, ftot, ffees
                side = pos.side
                px = apply_slippage(float(exit_price_raw), side, "exit",
                                    sc.stop_slippage_bps if reason in ("trail_stop", "stop") else sc.exit_slippage_bps)

                fp = funding_pnl(self.funding, self.hp, entry_time, t, side, qty,
                                 sc.funding_mode, sc.funding_multiplier)
                fees = qty * entry_price * sc.fee_rate + qty * px * sc.fee_rate
                gp = qty * (px - entry_price) if side == "long" else qty * (entry_price - px)
                pnl = gp - fees + fp

                trades.append({
                    "signal_time": signal_time,
                    "entry_time": entry_time,
                    "exit_time": t,
                    "dir": side,
                    "entry": entry_price,
                    "exit": px,
                    "qty": qty,
                    "gross_pnl": gp,
                    "fees": -fees,
                    "funding": fp,
                    "pnl": pnl,
                    "hold_hours": (t - entry_time).total_seconds() / 3600,
                    "reason": reason,
                    "coin": coin,
                })
                realized += pnl
                ftot += fp
                ffees += fees
                pos, qty, entry_price, entry_time, signal_time = None, 0.0, 0.0, None, None
                stop_price, trail_price, highest, bars_held = np.nan, np.nan, np.nan, 0

            # Check exits
            if pos is not None:
                if pos.side == "long":
                    if b["low"] <= trail_price:
                        do_exit("trail_stop", min(trail_price, b["open"]))
                        exited = True
                    elif not exited and b["low"] <= stop_price:
                        do_exit("stop", min(stop_price, b["open"]))
                        exited = True
                else:
                    if b["high"] >= trail_price:
                        do_exit("trail_stop", max(trail_price, b["open"]))
                        exited = True
                    elif not exited and b["high"] >= stop_price:
                        do_exit("stop", max(stop_price, b["open"]))
                        exited = True

                if not exited:
                    bars_held += 1
                    if bars_held >= strategy.max_hold_bars:
                        do_exit("max_hold", nb["open"])
                        exited = True

            # Check entries (next-bar execution = use nb["open"])
            if pos is None:
                sig = strategy.evaluate(bar, None)
                equity = sc.initial_capital + realized
                risk_usd = equity * strategy.risk_pct

                if sig == Signal.LONG:
                    ep = apply_slippage(float(nb["open"]), "long", "entry", sc.entry_slippage_bps)
                    dist = strategy.stop_atr_mult * atr
                    q = risk_usd / dist if dist > 0 else 0.0
                    if q > 0:
                        pos = Position(side="long", entry_price=ep, quantity=q,
                                       stop_price=ep - dist, entry_time=nt)
                        qty, entry_price, entry_time, signal_time = q, ep, nt, t
                        trail_price = ep - 3.0 * atr
                        highest = float(ep)
                        bars_held = 0

                elif sig == Signal.SHORT:
                    ep = apply_slippage(float(nb["open"]), "short", "entry", sc.entry_slippage_bps)
                    dist = strategy.stop_atr_mult * atr
                    q = risk_usd / dist if dist > 0 else 0.0
                    if q > 0:
                        pos = Position(side="short", entry_price=ep, quantity=q,
                                       stop_price=ep + dist, entry_time=nt)
                        qty, entry_price, entry_time, signal_time = q, ep, nt, t
                        trail_price = ep + 3.0 * atr
                        highest = float(ep)
                        bars_held = 0

            # Mark-to-market equity
            if pos is not None:
                op = qty * (b["close"] - entry_price) if pos.side == "long" else qty * (entry_price - b["close"])
            else:
                op = 0.0
            eq_rows.append({"time": t, "equity": sc.initial_capital + realized + op})

        # Close open position at EOD
        if pos is not None and entry_time is not None:
            t = df.index[-1]
            side = pos.side
            px = apply_slippage(float(df.iloc[-1]["close"]), side, "exit", sc.exit_slippage_bps)
            fp = funding_pnl(self.funding, self.hp, entry_time, t, side, qty,
                             sc.funding_mode, sc.funding_multiplier)
            fees = qty * entry_price * sc.fee_rate + qty * px * sc.fee_rate
            gp = qty * (px - entry_price) if side == "long" else qty * (entry_price - px)
            pnl = gp - fees + fp
            trades.append({
                "signal_time": signal_time,
                "entry_time": entry_time,
                "exit_time": t,
                "dir": side,
                "entry": entry_price,
                "exit": px,
                "qty": qty,
                "gross_pnl": gp,
                "fees": -fees,
                "funding": fp,
                "pnl": pnl,
                "hold_hours": (t - entry_time).total_seconds() / 3600,
                "reason": "eod",
                "coin": coin,
            })

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
    return {
        "return_pct": round(ret, 2), "cagr": round(cagr, 2), "max_dd": round(dd, 2),
        "pf": round(pf, 2) if np.isfinite(pf) else float("inf"), "wr": round(wr, 2),
        "trades": len(trades), "sharpe": round(sh, 3), "sortino": round(so, 3)
    }

def _native(o):
    if isinstance(o, (np.integer,)): return int(o)
    if isinstance(o, (np.floating,)): return float(o)
    if isinstance(o, dict): return {k: _native(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)): return [_native(v) for v in o]
    return o

# ── main ─────────────────────────────────────────────────────────────────────

def main():
    repo_root = Path(__file__).resolve().parents[3]
    out_dir = repo_root / "backtest-results" / "hyperliquid" / "v48_final_validation"
    out_dir.mkdir(parents=True, exist_ok=True)

    COIN = "BTC"
    INTERVAL = "1h"
    bpy = bars_per_year_for(INTERVAL)
    freq = freq_for(INTERVAL)

    end_ms = now_floor_ms(INTERVAL) - 1
    print(f"[v48-validation] Fetching {COIN} {INTERVAL}...")
    earliest = infer_earliest(COIN, INTERVAL)
    if not earliest:
        raise RuntimeError(f"No {INTERVAL} data for {COIN}")
    klines = fetch_klines(COIN, INTERVAL, int(earliest.timestamp()*1000), end_ms)
    rs, re, bar_count = longest_continuous_segment(klines, freq)
    strict = klines.loc[(klines.index >= rs) & (klines.index <= re)].copy()
    print(f"[v48-validation] Window: {rs.date()} → {re.date()} ({(re-rs).days}d, {len(strict)} bars)")

    funding = fetch_funding(COIN, int(rs.timestamp()*1000), int(re.timestamp()*1000))
    hp = strict["close"].reindex(funding.index, method="ffill")
    weeks = (re - rs).days / 7

    # Count raw signals for reference
    strategy = BTCMomentum1hStrategy()
    df_ind = strategy.compute_indicators(strict, pd.DataFrame())
    # Simulate signal counting (simplified, not accounting for warmup/exits)
    bullish = df_ind["close"] > df_ind["open"]
    bearish = df_ind["close"] < df_ind["open"]
    rsi_rising = df_ind["rsi"] > df_ind["rsi"].shift(2)
    rsi_falling = df_ind["rsi"] < df_ind["rsi"].shift(2)
    adx_ok = df_ind["adx"] > 20
    uptrend = (df_ind["plus_di"] > df_ind["minus_di"]) & adx_ok
    downtrend = (df_ind["minus_di"] > df_ind["plus_di"]) & adx_ok
    long_signals = (uptrend & (df_ind["rsi"] >= 35) & (df_ind["rsi"] <= 50) & bullish & rsi_rising).sum()
    short_signals = (downtrend & (df_ind["rsi"] >= 50) & (df_ind["rsi"] <= 65) & bearish & rsi_falling).sum()
    print(f"[v48-validation] Raw signals: {long_signals+short_signals} (L:{long_signals} S:{short_signals}) | ~{(long_signals+short_signals)/max(weeks,1):.1f}/week")

    scenarios = [
        ScenarioConfig("optimistic", 0.00045, 2.0, 2.0, 6.0, "none", 0.0, "Optimistic: low slippage, no funding"),
        ScenarioConfig("baseline",   0.00045, 3.0, 3.0, 8.0, "actual_signed", 1.0, "Baseline: realistic slippage, actual funding"),
        ScenarioConfig("stressed",   0.00045, 5.0, 5.0, 12.0, "stressed", 2.0, "Stressed: high slippage, 2x worst-case funding"),
    ]

    all_rows = []
    for sc in scenarios:
        print(f"[v48-validation] ▶ {sc.name}...", end=" ", flush=True)
        td, ed, costs = StrategyEngine(sc, funding, hp).run(strict, COIN)
        m = calc_metrics(td, ed, sc.initial_capital, bpy)
        tpw = m["trades"] / max(weeks, 1)
        row = {"scenario": sc.name, **m, **costs, "trades_per_week": round(tpw, 2)}
        all_rows.append(row)
        print(f"CAGR {m['cagr']:.1f}% PF {m['pf']:.2f} DD {m['max_dd']:.1f}% T {m['trades']}")

        if not td.empty:
            td.to_csv(out_dir / f"v48_{sc.name}_trades.csv", index=False)
        if not ed.empty:
            ed.to_csv(out_dir / f"v48_{sc.name}_equity.csv")

    # ── Summary ──────────────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("  v48 FINAL VALIDATION RESULTS")
    print("=" * 70)

    previous = {"cagr": 26.6, "max_dd": -4.7, "pf": 2.31, "trades": 30}

    print(f"\n  {'Metric':<18} {'Previous v48b':>14} {'Optimistic':>12} {'Baseline':>12} {'Stressed':>12}")
    print(f"  {'-'*70}")
    for m in ["cagr", "max_dd", "pf", "wr", "trades", "trades_per_week", "sharpe", "sortino"]:
        prev_val = previous.get(m, "—")
        vals = [str(r.get(m, "N/A")) for r in all_rows]
        print(f"  {m:<18} {str(prev_val):>14} {vals[0]:>12} {vals[1]:>12} {vals[2]:>12}")

    baseline_row = next((r for r in all_rows if r["scenario"] == "baseline"), None)
    if baseline_row:
        cagr_diff = baseline_row["cagr"] - previous["cagr"]
        dd_diff = baseline_row["max_dd"] - previous["max_dd"]
        pf_diff = baseline_row["pf"] - previous["pf"]
        t_diff = baseline_row["trades"] - previous["trades"]
        print(f"\n  Δ Baseline vs Previous:")
        print(f"    CAGR:   {cagr_diff:+.1f}%")
        print(f"    MaxDD:  {dd_diff:+.1f}%")
        print(f"    PF:     {pf_diff:+.2f}")
        print(f"    Trades: {t_diff:+d}")

        # Verdict
        print(f"\n  VERDICT:")
        if abs(cagr_diff) < 2.0 and abs(dd_diff) < 1.5 and abs(pf_diff) < 0.3 and abs(t_diff) <= 3:
            print("  ✅ PASS — Restructure does NOT affect performance. Metrics within tolerance.")
        else:
            print("  ⚠️  DIFFERENCE DETECTED — Metrics deviate from previous run beyond tolerance.")
            if abs(cagr_diff) >= 2.0:
                print(f"     • CAGR deviation {cagr_diff:+.1f}% is significant")
            if abs(dd_diff) >= 1.5:
                print(f"     • MaxDD deviation {dd_diff:+.1f}% is significant")
            if abs(pf_diff) >= 0.3:
                print(f"     • PF deviation {pf_diff:+.2f} is significant")
            if abs(t_diff) > 3:
                print(f"     • Trade count deviation {t_diff:+d} is significant")

    summary = {
        "previous_v48b": previous,
        "scenarios": {r["scenario"]: r for r in all_rows},
        "window": {"start": str(rs.date()), "end": str(re.date()), "bars": bar_count, "weeks": round(weeks, 1)},
    }
    (out_dir / "summary.json").write_text(json.dumps(_native(summary), indent=2))
    print(f"\n  → Results saved to {out_dir}")

if __name__ == "__main__":
    main()
