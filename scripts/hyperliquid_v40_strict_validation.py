#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
import requests


HYPERLIQUID_INFO = "https://api.hyperliquid.xyz/info"


def dt_to_ms(s: str) -> int:
    return int(datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)


def now_floor_4h_ms() -> int:
    now = pd.Timestamp.now(tz="UTC")
    floored_hour = (now.hour // 4) * 4
    floored = now.floor("D") + pd.Timedelta(hours=floored_hour)
    return int(floored.timestamp() * 1000)


def fetch_hyperliquid_klines(coin: str, interval: str, start_ms: int, end_ms: int, chunk_days: int) -> pd.DataFrame:
    out: List[dict] = []
    cur = start_ms
    chunk_ms = chunk_days * 24 * 60 * 60 * 1000
    while cur < end_ms:
        chunk_end = min(cur + chunk_ms, end_ms)
        payload = {
            "type": "candleSnapshot",
            "req": {"coin": coin, "interval": interval, "startTime": cur, "endTime": chunk_end},
        }
        r = requests.post(HYPERLIQUID_INFO, json=payload, timeout=30)
        r.raise_for_status()
        data = r.json()
        out.extend(data)
        cur = chunk_end
        time.sleep(0.05)

    if not out:
        raise RuntimeError(f"No Hyperliquid data for {coin} {interval}")

    df = pd.DataFrame(out)
    df["t"] = pd.to_datetime(df["t"], unit="ms", utc=True)
    for c in ["o", "h", "l", "c", "v"]:
        df[c] = df[c].astype(float)
    df = df.rename(columns={"t": "time", "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
    df = df.set_index("time")[["open", "high", "low", "close", "volume"]].sort_index()
    df = df[~df.index.duplicated(keep="last")]
    return df


def fetch_funding_history(coin: str, start_ms: int, end_ms: int, chunk_hours: int = 400) -> pd.DataFrame:
    out: List[dict] = []
    cur = start_ms
    chunk_ms = chunk_hours * 60 * 60 * 1000
    while cur < end_ms:
        chunk_end = min(cur + chunk_ms, end_ms)
        payload = {"type": "fundingHistory", "coin": coin, "startTime": cur, "endTime": chunk_end}
        r = requests.post(HYPERLIQUID_INFO, json=payload, timeout=30)
        r.raise_for_status()
        data = r.json()
        out.extend(data)
        cur = chunk_end + 1
        time.sleep(0.05)

    if not out:
        raise RuntimeError(f"No funding history for {coin}")

    df = pd.DataFrame(out)
    df["time"] = pd.to_datetime(df["time"], unit="ms", utc=True)
    df["fundingRate"] = df["fundingRate"].astype(float)
    if "premium" in df.columns:
        df["premium"] = df["premium"].astype(float)
    cols = ["fundingRate"] + (["premium"] if "premium" in df.columns else [])
    df = df.set_index("time")[cols].sort_index()
    df = df[~df.index.duplicated(keep="last")]
    return df


def infer_earliest_hyperliquid_4h(coin: str = "BTC") -> pd.Timestamp:
    probes = [
        ("2021-01-01", "2021-12-31"),
        ("2022-01-01", "2022-12-31"),
        ("2023-01-01", "2023-12-31"),
    ]
    first = None
    for start, end in probes:
        payload = {
            "type": "candleSnapshot",
            "req": {"coin": coin, "interval": "4h", "startTime": dt_to_ms(start), "endTime": dt_to_ms(end)},
        }
        r = requests.post(HYPERLIQUID_INFO, json=payload, timeout=30)
        r.raise_for_status()
        data = r.json()
        if data:
            ts = pd.to_datetime(data[0]["t"], unit="ms", utc=True)
            first = ts if first is None else min(first, ts)
    if first is None:
        raise RuntimeError("Could not infer earliest Hyperliquid 4h BTC candle")
    return first


def missing_bar_stats(df: pd.DataFrame, freq: str) -> Dict[str, object]:
    expected = pd.date_range(df.index.min(), df.index.max(), freq=freq, tz="UTC")
    missing = expected.difference(df.index)
    return {
        "bars": int(len(df)),
        "duplicates": int(df.index.duplicated().sum()),
        "missing_bars": int(len(missing)),
        "first": df.index.min().isoformat(),
        "last": df.index.max().isoformat(),
        "sample_missing": [ts.isoformat() for ts in missing[:10]],
    }


def longest_continuous_segment(df: pd.DataFrame, freq: str) -> Tuple[pd.Timestamp, pd.Timestamp, int]:
    if df.empty:
        raise RuntimeError("Cannot compute longest continuous segment on empty dataframe")
    step = pd.Timedelta(freq)
    segments: List[Tuple[pd.Timestamp, pd.Timestamp, int]] = []
    start = df.index[0]
    prev = df.index[0]
    count = 1
    for ts in df.index[1:]:
        if ts - prev == step:
            count += 1
        else:
            segments.append((start, prev, count))
            start = ts
            count = 1
        prev = ts
    segments.append((start, prev, count))
    return max(segments, key=lambda x: x[2])


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [(df["high"] - df["low"]), (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False).mean()


def build_features(df_4h: pd.DataFrame, df_daily: pd.DataFrame, warmup_daily_start: str = "2021-01-01") -> pd.DataFrame:
    out = df_4h.copy()
    out["ema50"] = out["close"].ewm(span=50, adjust=False).mean()
    out["ema200"] = out["close"].ewm(span=200, adjust=False).mean()
    out["atr"] = atr(out, 14)

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
    warmup_bars: int = 220


def apply_slippage(price: float, side: str, action: str, bps: float) -> float:
    bump = bps / 10_000.0
    if action == "entry":
        return price * (1 + bump) if side == "long" else price * (1 - bump)
    return price * (1 - bump) if side == "long" else price * (1 + bump)


def funding_pnl_for_window(
    funding: pd.DataFrame,
    hourly_prices: pd.Series,
    entry_time: pd.Timestamp,
    exit_time: pd.Timestamp,
    side: str,
    qty: float,
    mode: str,
    multiplier: float,
) -> float:
    window = funding.loc[(funding.index > entry_time) & (funding.index <= exit_time)].copy()
    if window.empty:
        return 0.0

    prices = hourly_prices.reindex(window.index, method="ffill")
    sign = 1.0 if side == "long" else -1.0
    signed = -sign * window["fundingRate"] * qty * prices

    if mode == "actual_signed":
        pnl = signed
    elif mode == "adverse_only":
        pnl = signed.clip(upper=0.0) * multiplier
        return float(pnl.sum())
    else:
        raise ValueError(f"Unknown funding mode: {mode}")

    return float((pnl * multiplier).sum())


class StrictEngine:
    def __init__(self, scenario: ScenarioConfig, funding: pd.DataFrame, hourly_prices: pd.Series):
        self.scenario = scenario
        self.funding = funding
        self.hourly_prices = hourly_prices

    def trade_fee(self, qty: float, price: float) -> float:
        return qty * price * self.scenario.fee_rate

    def run(self, df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame, Dict[str, float]]:
        position = 0
        qty = 0.0
        entry = 0.0
        entry_time: pd.Timestamp | None = None
        signal_time: pd.Timestamp | None = None
        stop = np.nan
        realized = 0.0
        trades: List[dict] = []
        equity_rows: List[dict] = []
        funding_total = 0.0
        fee_total = 0.0

        for i in range(self.scenario.warmup_bars, len(df) - 1):
            t = df.index[i]
            b = df.iloc[i]
            next_bar = df.iloc[i + 1]
            next_time = df.index[i + 1]

            if np.isnan(b["atr"]) or np.isnan(b["ema50"]) or np.isnan(b["ema200"]) or np.isnan(b["ema200_daily"]):
                continue

            exited_this_bar = False

            if position == 1:
                stop_hit = b["low"] <= stop
                if stop_hit:
                    raw_exit = min(float(stop), float(b["open"]))
                    exit_px = apply_slippage(raw_exit, "long", "exit", self.scenario.stop_slippage_bps)
                    funding_pnl = funding_pnl_for_window(
                        self.funding,
                        self.hourly_prices,
                        entry_time,
                        t,
                        "long",
                        qty,
                        self.scenario.funding_mode,
                        self.scenario.funding_multiplier,
                    )
                    fees = self.trade_fee(qty, entry) + self.trade_fee(qty, exit_px)
                    pnl = qty * (exit_px - entry) - fees + funding_pnl
                    realized += pnl
                    funding_total += funding_pnl
                    fee_total += fees
                    trades.append({
                        "signal_time": signal_time,
                        "entry_time": entry_time,
                        "exit_time": t,
                        "dir": "long",
                        "entry": entry,
                        "exit": exit_px,
                        "qty": qty,
                        "gross_pnl": qty * (exit_px - entry),
                        "fees": -fees,
                        "funding": funding_pnl,
                        "pnl": pnl,
                        "hold_hours": (t - entry_time).total_seconds() / 3600,
                        "reason": "stop",
                    })
                    position, qty, entry, entry_time, signal_time, stop = 0, 0.0, 0.0, None, None, np.nan
                    exited_this_bar = True

            elif position == -1:
                stop_hit = b["high"] >= stop
                if stop_hit:
                    raw_exit = max(float(stop), float(b["open"]))
                    exit_px = apply_slippage(raw_exit, "short", "exit", self.scenario.stop_slippage_bps)
                    funding_pnl = funding_pnl_for_window(
                        self.funding,
                        self.hourly_prices,
                        entry_time,
                        t,
                        "short",
                        qty,
                        self.scenario.funding_mode,
                        self.scenario.funding_multiplier,
                    )
                    fees = self.trade_fee(qty, entry) + self.trade_fee(qty, exit_px)
                    pnl = qty * (entry - exit_px) - fees + funding_pnl
                    realized += pnl
                    funding_total += funding_pnl
                    fee_total += fees
                    trades.append({
                        "signal_time": signal_time,
                        "entry_time": entry_time,
                        "exit_time": t,
                        "dir": "short",
                        "entry": entry,
                        "exit": exit_px,
                        "qty": qty,
                        "gross_pnl": qty * (entry - exit_px),
                        "fees": -fees,
                        "funding": funding_pnl,
                        "pnl": pnl,
                        "hold_hours": (t - entry_time).total_seconds() / 3600,
                        "reason": "stop",
                    })
                    position, qty, entry, entry_time, signal_time, stop = 0, 0.0, 0.0, None, None, np.nan
                    exited_this_bar = True

            if not exited_this_bar and position == 1 and bool(b["long_exit_signal"]):
                exit_px = apply_slippage(float(next_bar["open"]), "long", "exit", self.scenario.exit_slippage_bps)
                funding_pnl = funding_pnl_for_window(
                    self.funding,
                    self.hourly_prices,
                    entry_time,
                    next_time,
                    "long",
                    qty,
                    self.scenario.funding_mode,
                    self.scenario.funding_multiplier,
                )
                fees = self.trade_fee(qty, entry) + self.trade_fee(qty, exit_px)
                pnl = qty * (exit_px - entry) - fees + funding_pnl
                realized += pnl
                funding_total += funding_pnl
                fee_total += fees
                trades.append({
                    "signal_time": signal_time,
                    "entry_time": entry_time,
                    "exit_time": next_time,
                    "dir": "long",
                    "entry": entry,
                    "exit": exit_px,
                    "qty": qty,
                    "gross_pnl": qty * (exit_px - entry),
                    "fees": -fees,
                    "funding": funding_pnl,
                    "pnl": pnl,
                    "hold_hours": (next_time - entry_time).total_seconds() / 3600,
                    "reason": "ema_inverse_next_open",
                })
                position, qty, entry, entry_time, signal_time, stop = 0, 0.0, 0.0, None, None, np.nan

            elif not exited_this_bar and position == -1 and bool(b["short_exit_signal"]):
                exit_px = apply_slippage(float(next_bar["open"]), "short", "exit", self.scenario.exit_slippage_bps)
                funding_pnl = funding_pnl_for_window(
                    self.funding,
                    self.hourly_prices,
                    entry_time,
                    next_time,
                    "short",
                    qty,
                    self.scenario.funding_mode,
                    self.scenario.funding_multiplier,
                )
                fees = self.trade_fee(qty, entry) + self.trade_fee(qty, exit_px)
                pnl = qty * (entry - exit_px) - fees + funding_pnl
                realized += pnl
                funding_total += funding_pnl
                fee_total += fees
                trades.append({
                    "signal_time": signal_time,
                    "entry_time": entry_time,
                    "exit_time": next_time,
                    "dir": "short",
                    "entry": entry,
                    "exit": exit_px,
                    "qty": qty,
                    "gross_pnl": qty * (entry - exit_px),
                    "fees": -fees,
                    "funding": funding_pnl,
                    "pnl": pnl,
                    "hold_hours": (next_time - entry_time).total_seconds() / 3600,
                    "reason": "ema_inverse_next_open",
                })
                position, qty, entry, entry_time, signal_time, stop = 0, 0.0, 0.0, None, None, np.nan

            if position == 0:
                equity_now = self.scenario.initial_capital + realized
                risk_usd = equity_now * self.scenario.risk_pct
                atr_dist = self.scenario.stop_atr_mult * float(b["atr"])

                if bool(b["long_signal"]) and atr_dist > 0:
                    raw_entry = float(next_bar["open"])
                    entry_px = apply_slippage(raw_entry, "long", "entry", self.scenario.entry_slippage_bps)
                    stop_px = entry_px - atr_dist
                    q = risk_usd / atr_dist
                    if q > 0:
                        position, qty, entry, entry_time, signal_time, stop = 1, float(q), entry_px, next_time, t, float(stop_px)

                elif bool(b["short_signal"]) and atr_dist > 0:
                    raw_entry = float(next_bar["open"])
                    entry_px = apply_slippage(raw_entry, "short", "entry", self.scenario.entry_slippage_bps)
                    stop_px = entry_px + atr_dist
                    q = risk_usd / atr_dist
                    if q > 0:
                        position, qty, entry, entry_time, signal_time, stop = -1, float(q), entry_px, next_time, t, float(stop_px)

            open_pnl = 0.0
            if position == 1:
                open_pnl = qty * (b["close"] - entry)
            elif position == -1:
                open_pnl = qty * (entry - b["close"])
            equity_rows.append({"time": t, "equity": self.scenario.initial_capital + realized + open_pnl})

        if position != 0 and entry_time is not None:
            t = df.index[-1]
            px = float(df.iloc[-1]["close"])
            exit_px = apply_slippage(px, "long" if position == 1 else "short", "exit", self.scenario.exit_slippage_bps)
            side = "long" if position == 1 else "short"
            gross_pnl = qty * (exit_px - entry) if position == 1 else qty * (entry - exit_px)
            funding_pnl = funding_pnl_for_window(
                self.funding,
                self.hourly_prices,
                entry_time,
                t,
                side,
                qty,
                self.scenario.funding_mode,
                self.scenario.funding_multiplier,
            )
            fees = self.trade_fee(qty, entry) + self.trade_fee(qty, exit_px)
            pnl = gross_pnl - fees + funding_pnl
            funding_total += funding_pnl
            fee_total += fees
            trades.append({
                "signal_time": signal_time,
                "entry_time": entry_time,
                "exit_time": t,
                "dir": side,
                "entry": entry,
                "exit": exit_px,
                "qty": qty,
                "gross_pnl": gross_pnl,
                "fees": -fees,
                "funding": funding_pnl,
                "pnl": pnl,
                "hold_hours": (t - entry_time).total_seconds() / 3600,
                "reason": "eod",
            })

        trades_df = pd.DataFrame(trades)
        eq_df = pd.DataFrame(equity_rows).set_index("time") if equity_rows else pd.DataFrame(columns=["equity"])
        cost_breakdown = {"fees_total": round(float(fee_total), 2), "funding_total": round(float(funding_total), 2)}
        return trades_df, eq_df, cost_breakdown


def sortino_from_equity(eq: pd.Series) -> float:
    rets = eq.pct_change().dropna()
    if rets.empty:
        return 0.0
    downside = rets[rets < 0]
    if downside.std() == 0 or pd.isna(downside.std()):
        return 0.0
    return float(rets.mean() / downside.std() * np.sqrt(2190))


def metrics(trades: pd.DataFrame, eq: pd.DataFrame, initial_capital: float) -> Dict[str, float]:
    if eq.empty:
        return {k: 0.0 for k in ["return_pct", "cagr", "max_dd", "pf", "wr", "trades", "sharpe", "sortino"]}

    eq_s = eq["equity"].dropna()
    ret = (eq_s.iloc[-1] / initial_capital - 1) * 100
    years = (eq_s.index[-1] - eq_s.index[0]).total_seconds() / (365.25 * 86400)
    cagr = ((eq_s.iloc[-1] / initial_capital) ** (1 / years) - 1) * 100 if years > 0 else 0.0
    peak = eq_s.cummax()
    dd = (eq_s / peak - 1) * 100
    max_dd = float(dd.min())

    rets = eq_s.pct_change().dropna()
    sharpe = float(rets.mean() / rets.std() * np.sqrt(2190)) if len(rets) > 1 and rets.std() > 0 else 0.0
    sortino = sortino_from_equity(eq_s)

    if trades.empty:
        pf = 0.0
        wr = 0.0
    else:
        pnls = trades["pnl"]
        wins = pnls[pnls > 0]
        losses = pnls[pnls <= 0]
        wr = float((pnls > 0).mean() * 100)
        pf = float(wins.sum() / abs(losses.sum())) if len(losses) and abs(losses.sum()) > 0 else math.inf

    return {
        "return_pct": round(float(ret), 2),
        "cagr": round(float(cagr), 2),
        "max_dd": round(max_dd, 2),
        "pf": round(float(pf), 2) if np.isfinite(pf) else float("inf"),
        "wr": round(float(wr), 2),
        "trades": int(len(trades)),
        "sharpe": round(float(sharpe), 3),
        "sortino": round(float(sortino), 3),
    }


def yearly_returns(eq: pd.DataFrame, initial_capital: float) -> pd.DataFrame:
    if eq.empty:
        return pd.DataFrame(columns=["year", "return_pct"])
    yearly = eq["equity"].resample("YS").last().dropna()
    prev = yearly.shift(1)
    if len(prev) > 0:
        prev.iloc[0] = initial_capital
    out = ((yearly / prev - 1) * 100).to_frame("return_pct")
    out.index = out.index.year
    out.index.name = "year"
    return out.reset_index()


def trade_distribution(trades: pd.DataFrame) -> Dict[str, float]:
    if trades.empty:
        return {
            "avg_trade_pnl": 0.0,
            "median_trade_pnl": 0.0,
            "avg_hold_hours": 0.0,
            "median_hold_hours": 0.0,
        }
    return {
        "avg_trade_pnl": round(float(trades["pnl"].mean()), 2),
        "median_trade_pnl": round(float(trades["pnl"].median()), 2),
        "avg_hold_hours": round(float(trades["hold_hours"].mean()), 2),
        "median_hold_hours": round(float(trades["hold_hours"].median()), 2),
    }


def liquidity_stats(trades: pd.DataFrame, df_4h: pd.DataFrame) -> Dict[str, float]:
    if trades.empty:
        return {"max_qty_pct_of_bar_volume": 0.0, "p95_qty_pct_of_bar_volume": 0.0}
    volume = df_4h["volume"].rename("bar_volume")
    merged = trades.merge(volume, left_on="entry_time", right_index=True, how="left")
    pct = merged["qty"] / merged["bar_volume"] * 100
    return {
        "max_qty_pct_of_bar_volume": round(float(pct.max()), 6),
        "p95_qty_pct_of_bar_volume": round(float(pct.quantile(0.95)), 6),
    }


def buy_hold_with_funding(
    df_4h: pd.DataFrame,
    funding: pd.DataFrame,
    hourly_prices: pd.Series,
    fee_rate: float,
    initial_capital: float,
) -> Dict[str, float]:
    start_t = df_4h.index[1]
    end_t = df_4h.index[-1]
    entry_px = float(df_4h.iloc[1]["open"]) * (1 + fee_rate)
    qty = initial_capital / entry_px
    exit_px = float(df_4h.iloc[-1]["close"]) * (1 - fee_rate)
    fees = qty * entry_px * fee_rate + qty * exit_px * fee_rate
    funding_pnl = funding_pnl_for_window(funding, hourly_prices, start_t, end_t, "long", qty, "actual_signed", 1.0)
    final_equity = initial_capital + qty * (exit_px - entry_px) - fees + funding_pnl
    ret = (final_equity / initial_capital - 1) * 100
    years = (end_t - start_t).total_seconds() / (365.25 * 86400)
    cagr = ((final_equity / initial_capital) ** (1 / years) - 1) * 100 if years > 0 else 0.0
    eq = (df_4h["close"] / entry_px) * initial_capital
    dd = (eq / eq.cummax() - 1) * 100
    return {
        "return_pct": round(float(ret), 2),
        "cagr": round(float(cagr), 2),
        "max_dd": round(float(dd.min()), 2),
        "funding_pnl": round(float(funding_pnl), 2),
    }


def load_legacy_overlap_summary(repo_root: Path) -> Dict[str, float] | None:
    p = repo_root / "docs/backtests/v40_hyperliquid_validation/validation_summary.json"
    if not p.exists():
        return None
    data = json.loads(p.read_text())
    return data.get("hyperliquid_overlap")


def write_report(
    out_dir: Path,
    diagnostics: Dict[str, object],
    scenario_table: pd.DataFrame,
    baseline_buy_hold: Dict[str, float],
    recommendation: str,
    verdict: str,
) -> None:
    headers = list(scenario_table.columns)
    markdown_lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for _, row in scenario_table.iterrows():
        markdown_lines.append("| " + " | ".join(str(row[h]) for h in headers) + " |")

    lines = [
        "# v40 Hyperliquid Strict Validation",
        "",
        f"**Verdict:** {verdict}",
        "",
        "## Assumptions",
        "- Strategy logic unchanged: BTC only, 4h execution, daily EMA200 regime filter, EMA50/EMA200 trend logic, ATR(14) x 3 stop, 2% risk.",
        "- Reliable test window restricted to the longest continuous Hyperliquid 4h segment to avoid missing-bar fabrication.",
        "- Entries/exits triggered by bar-close signals execute on the next 4h open.",
        "- Stop losses use conservative stop-market handling: worse of stop vs bar open, plus adverse stop slippage.",
        "- Hyperliquid taker fees applied on every entry/exit.",
        "- Exact Hyperliquid historical hourly funding was fetched and applied; because full public 1h candle history is capped, funding notional is approximated with forward-filled 4h closes.",
        "- Stressed funding keeps only adverse funding and magnifies it.",
        "- Daily EMA200 regime uses the previous completed daily bar to avoid look-ahead leakage.",
        "",
        "## Scenario Table",
        *markdown_lines,
        "",
        "## Baseline",
        f"- Passive long buy-and-hold on the same overlap window: return {baseline_buy_hold['return_pct']}%, CAGR {baseline_buy_hold['cagr']}%, max drawdown {baseline_buy_hold['max_dd']}%, funding pnl {baseline_buy_hold['funding_pnl']}.",
        "",
        "## Recommendation",
        recommendation,
        "",
        "## Diagnostics",
        "```json",
        json.dumps(diagnostics, indent=2),
        "```",
    ]
    (out_dir / "report.md").write_text("\n".join(lines))


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    out_dir = repo_root / "docs/backtests/v40_hyperliquid_strict_validation"
    out_dir.mkdir(parents=True, exist_ok=True)

    end_ms = now_floor_4h_ms() - 1
    earliest_hl_4h = infer_earliest_hyperliquid_4h("BTC")
    start_hl_4h_ms = int(earliest_hl_4h.timestamp() * 1000)
    start_daily_warmup_ms = dt_to_ms("2021-01-01")

    hyper_4h = fetch_hyperliquid_klines("BTC", "4h", start_hl_4h_ms, end_ms, chunk_days=120)
    hyper_1d = fetch_hyperliquid_klines("BTC", "1d", start_daily_warmup_ms, end_ms, chunk_days=365)

    reliable_start, reliable_end, reliable_bars = longest_continuous_segment(hyper_4h, "4h")
    strict_4h = hyper_4h.loc[(hyper_4h.index >= reliable_start) & (hyper_4h.index <= reliable_end)].copy()
    strict_1d = hyper_1d.loc[hyper_1d.index <= reliable_end].copy()
    funding = fetch_funding_history("BTC", int(reliable_start.timestamp() * 1000), int(reliable_end.timestamp() * 1000) + 1)

    features = build_features(strict_4h, strict_1d)
    hourly_prices = strict_4h["close"].reindex(funding.index, method="ffill")

    scenarios = [
        ScenarioConfig(
            name="optimistic_plausible",
            fee_rate=0.00045,
            entry_slippage_bps=1.0,
            exit_slippage_bps=1.0,
            stop_slippage_bps=3.0,
            funding_mode="actual_signed",
            funding_multiplier=1.0,
            description="Next-open execution, taker fees, light slippage, exact signed funding",
        ),
        ScenarioConfig(
            name="baseline_realistic",
            fee_rate=0.00045,
            entry_slippage_bps=3.0,
            exit_slippage_bps=3.0,
            stop_slippage_bps=8.0,
            funding_mode="actual_signed",
            funding_multiplier=1.0,
            description="Next-open execution, taker fees, moderate slippage, exact signed funding",
        ),
        ScenarioConfig(
            name="stressed_conservative",
            fee_rate=0.00045,
            entry_slippage_bps=6.0,
            exit_slippage_bps=6.0,
            stop_slippage_bps=15.0,
            funding_mode="adverse_only",
            funding_multiplier=1.5,
            description="Next-open execution, taker fees, heavy slippage, stop stress, only adverse funding counted and magnified",
        ),
    ]

    scenario_rows: List[dict] = []
    scenario_details: Dict[str, object] = {}
    for scenario in scenarios:
        trades, eq, cost_breakdown = StrictEngine(scenario, funding, hourly_prices).run(features)
        summary = metrics(trades, eq, scenario.initial_capital)
        dist = trade_distribution(trades)
        liq = liquidity_stats(trades, strict_4h)
        yearly = yearly_returns(eq, scenario.initial_capital)

        trades.to_csv(out_dir / f"{scenario.name}_trades.csv", index=False)
        eq.to_csv(out_dir / f"{scenario.name}_equity.csv")
        yearly.to_csv(out_dir / f"{scenario.name}_yearly.csv", index=False)
        pd.DataFrame([summary | cost_breakdown | dist | liq]).to_csv(out_dir / f"{scenario.name}_summary.csv", index=False)

        scenario_rows.append({
            "scenario": scenario.name,
            "description": scenario.description,
            **summary,
            **cost_breakdown,
        })
        scenario_details[scenario.name] = {
            "config": asdict(scenario),
            "summary": summary,
            "cost_breakdown": cost_breakdown,
            "trade_distribution": dist,
            "liquidity": liq,
        }

    scenario_table = pd.DataFrame(scenario_rows)
    scenario_table.to_csv(out_dir / "scenario_table.csv", index=False)

    legacy_overlap = load_legacy_overlap_summary(repo_root)
    buy_hold = buy_hold_with_funding(strict_4h, funding, hourly_prices, 0.00045, 10_000.0)

    diagnostics = {
        "generated_at_utc": pd.Timestamp.now(tz="UTC").isoformat(),
        "methodology": {
            "execution": "next 4h bar open for signal-driven entries/exits; intrabar conservative stop-market fill",
            "daily_regime_alignment": "previous completed daily EMA200 shifted by one day before 4h forward-fill",
            "funding": "exact Hyperliquid hourly fundingHistory for BTC, notional approximated with forward-filled 4h closes",
            "reliable_window_only": True,
        },
        "data_integrity": {
            "hyperliquid_4h_full": missing_bar_stats(hyper_4h, "4h"),
            "reliable_continuous_4h_segment": {
                "start": reliable_start.isoformat(),
                "end": reliable_end.isoformat(),
                "bars": reliable_bars,
            },
            "strict_4h_window": missing_bar_stats(strict_4h, "4h"),
            "strict_1d_window": missing_bar_stats(strict_1d, "1D"),
            "funding_window": missing_bar_stats(
                funding.set_index(funding.index.floor("1h"))[~funding.set_index(funding.index.floor("1h")).index.duplicated(keep="last")],
                "1h",
            ),
        },
        "legacy_overlap_summary": legacy_overlap,
        "buy_hold_overlap_same_window": buy_hold,
        "scenario_details": scenario_details,
    }
    (out_dir / "diagnostics.json").write_text(json.dumps(diagnostics, indent=2))
    (out_dir / "summary.json").write_text(json.dumps({"scenarios": scenario_rows, "buy_hold": buy_hold, "legacy_overlap": legacy_overlap}, indent=2))

    baseline = next(row for row in scenario_rows if row["scenario"] == "baseline_realistic")
    stressed = next(row for row in scenario_rows if row["scenario"] == "stressed_conservative")

    if stressed["return_pct"] <= 0 or stressed["pf"] < 1.0 or baseline["return_pct"] <= 0:
        verdict = "NO-GO"
        recommendation = (
            "The edge does not survive conservative execution stress cleanly enough for live deployment. "
            "Use this only for testnet validation of the plumbing, and do not move to live until the strategy is reworked or materially improved."
        )
    elif baseline["pf"] < 1.2 or baseline["max_dd"] < -20 or baseline["cagr"] < buy_hold["cagr"] * 0.4:
        verdict = "CONDITIONAL"
        recommendation = (
            "The strategy survives realistic execution, but the edge is modest and it badly underperforms passive long BTC on the same window. "
            "That means this is suitable for testnet validation and, at most, extremely tiny live sizing while you validate real slippage, stop fills, and funding drift."
        )
    else:
        verdict = "GO"
        recommendation = (
            "The strategy survives strict realism well enough to justify a tiny live pilot, "
            "but only with ongoing monitoring of slippage, stop execution, and funding drift."
        )

    write_report(out_dir, diagnostics, scenario_table[["scenario", "return_pct", "cagr", "max_dd", "pf", "wr", "trades", "sharpe", "sortino", "fees_total", "funding_total"]], buy_hold, recommendation, verdict)

    print(json.dumps({"verdict": verdict, "baseline": baseline, "stressed": stressed, "buy_hold": buy_hold, "out_dir": str(out_dir)}, indent=2))


if __name__ == "__main__":
    main()
