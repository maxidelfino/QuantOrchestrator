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


BINANCE_FAPI_KLINES = "https://fapi.binance.com/fapi/v1/klines"
HYPERLIQUID_INFO = "https://api.hyperliquid.xyz/info"


def dt_to_ms(s: str) -> int:
    return int(datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)


def now_floor_4h_ms() -> int:
    now = pd.Timestamp.now(tz="UTC")
    floored_hour = (now.hour // 4) * 4
    floored = now.floor("D") + pd.Timedelta(hours=floored_hour)
    return int(floored.timestamp() * 1000)


def fetch_binance_klines(symbol: str, interval: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    out: List[list] = []
    cur = start_ms
    while cur < end_ms:
        params = {"symbol": symbol, "interval": interval, "startTime": cur, "endTime": end_ms, "limit": 1500}
        r = requests.get(BINANCE_FAPI_KLINES, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        if not data:
            break
        out.extend(data)
        last_open = data[-1][0]
        if len(data) < 1500 or last_open >= end_ms:
            break
        cur = last_open + 1
        time.sleep(0.05)

    if not out:
        raise RuntimeError(f"No Binance data for {symbol} {interval}")

    df = pd.DataFrame(
        out,
        columns=[
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore",
        ],
    )
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = df[c].astype(float)
    df = df.set_index("open_time")[["open", "high", "low", "close", "volume"]].sort_index()
    df = df[~df.index.duplicated(keep="last")]
    return df


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


@dataclass
class BacktestConfig:
    name: str
    fee_rate: float
    slippage_rate: float = 0.0
    initial_capital: float = 10_000.0
    risk_pct: float = 0.02
    ema_fast: int = 50
    ema_slow: int = 200
    ema_regime_daily: int = 200
    atr_period: int = 14
    stop_atr_mult: float = 3.0
    warmup_bars: int = 220


def build_features(df_4h: pd.DataFrame, df_daily: pd.DataFrame, cfg: BacktestConfig) -> pd.DataFrame:
    out = df_4h.copy()
    out["ema50"] = out["close"].ewm(span=cfg.ema_fast, adjust=False).mean()
    out["ema200"] = out["close"].ewm(span=cfg.ema_slow, adjust=False).mean()
    out["atr"] = atr(out, cfg.atr_period)

    daily = df_daily.copy()
    daily["ema200_daily"] = daily["close"].ewm(span=cfg.ema_regime_daily, adjust=False).mean()
    regime = daily[["ema200_daily"]].reindex(out.index, method="ffill")
    out["ema200_daily"] = regime["ema200_daily"]

    out["regime_long"] = (out["close"] > out["ema200_daily"]).astype(int)
    out["regime_short"] = (out["close"] < out["ema200_daily"]).astype(int)
    out["long_signal"] = (out["close"] > out["ema50"]) & (out["ema50"] > out["ema200"]) & (out["regime_long"] == 1)
    out["short_signal"] = (out["close"] < out["ema50"]) & (out["ema50"] < out["ema200"]) & (out["regime_short"] == 1)
    out["long_exit_signal"] = out["ema50"] < out["ema200"]
    out["short_exit_signal"] = out["ema50"] > out["ema200"]
    return out


class Engine:
    def __init__(self, cfg: BacktestConfig):
        self.cfg = cfg

    def _trade_fee(self, qty: float, price: float) -> float:
        return qty * price * (self.cfg.fee_rate + self.cfg.slippage_rate)

    def run(self, df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
        position = 0
        qty = 0.0
        entry = 0.0
        entry_bar = -1
        stop = np.nan
        realized = 0.0
        trades: List[dict] = []
        equity_rows: List[dict] = []

        for i in range(self.cfg.warmup_bars, len(df)):
            t = df.index[i]
            b = df.iloc[i]

            if np.isnan(b["atr"]) or np.isnan(b["ema50"]) or np.isnan(b["ema200"]) or np.isnan(b["ema200_daily"]):
                continue

            if position == 1:
                sl_hit = b["low"] <= stop
                exit_sig = bool(b["long_exit_signal"])
                if sl_hit or exit_sig:
                    exit_px = float(stop) if sl_hit else float(b["close"])
                    pnl = qty * (exit_px - entry) - self._trade_fee(qty, entry) - self._trade_fee(qty, exit_px)
                    realized += pnl
                    trades.append({
                        "entry_time": df.index[entry_bar], "exit_time": t, "dir": "long",
                        "entry": entry, "exit": exit_px, "qty": qty, "pnl": pnl,
                        "reason": "stop" if sl_hit else "ema_inverse",
                    })
                    position, qty, entry, stop = 0, 0.0, 0.0, np.nan

            elif position == -1:
                sl_hit = b["high"] >= stop
                exit_sig = bool(b["short_exit_signal"])
                if sl_hit or exit_sig:
                    exit_px = float(stop) if sl_hit else float(b["close"])
                    pnl = qty * (entry - exit_px) - self._trade_fee(qty, entry) - self._trade_fee(qty, exit_px)
                    realized += pnl
                    trades.append({
                        "entry_time": df.index[entry_bar], "exit_time": t, "dir": "short",
                        "entry": entry, "exit": exit_px, "qty": qty, "pnl": pnl,
                        "reason": "stop" if sl_hit else "ema_inverse",
                    })
                    position, qty, entry, stop = 0, 0.0, 0.0, np.nan

            if position == 0:
                px = float(b["close"])
                equity_now = self.cfg.initial_capital + realized
                risk_usd = equity_now * self.cfg.risk_pct

                if bool(b["long_signal"]):
                    stop_px = px - self.cfg.stop_atr_mult * float(b["atr"])
                    dist = px - stop_px
                    if dist > 0:
                        q = risk_usd / dist
                        if q > 0:
                            position, qty, entry, entry_bar, stop = 1, float(q), px, i, float(stop_px)

                elif bool(b["short_signal"]):
                    stop_px = px + self.cfg.stop_atr_mult * float(b["atr"])
                    dist = stop_px - px
                    if dist > 0:
                        q = risk_usd / dist
                        if q > 0:
                            position, qty, entry, entry_bar, stop = -1, float(q), px, i, float(stop_px)

            open_pnl = 0.0
            if position == 1:
                open_pnl = qty * (b["close"] - entry)
            elif position == -1:
                open_pnl = qty * (entry - b["close"])
            equity_rows.append({"time": t, "equity": self.cfg.initial_capital + realized + open_pnl})

        if position != 0:
            t = df.index[-1]
            px = float(df.iloc[-1]["close"])
            pnl = (qty * (px - entry) if position == 1 else qty * (entry - px))
            pnl -= self._trade_fee(qty, entry) + self._trade_fee(qty, px)
            trades.append({
                "entry_time": df.index[entry_bar], "exit_time": t,
                "dir": "long" if position == 1 else "short",
                "entry": entry, "exit": px, "qty": qty, "pnl": pnl, "reason": "eod",
            })

        tr = pd.DataFrame(trades)
        eq = pd.DataFrame(equity_rows).set_index("time") if equity_rows else pd.DataFrame(columns=["equity"])
        return tr, eq


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


def buy_hold_metrics(df: pd.DataFrame) -> Dict[str, float]:
    start = float(df["close"].iloc[0])
    end = float(df["close"].iloc[-1])
    ret = (end / start - 1) * 100
    years = (df.index[-1] - df.index[0]).total_seconds() / (365.25 * 86400)
    cagr = ((end / start) ** (1 / years) - 1) * 100 if years > 0 else 0.0
    eq = df["close"] / start
    dd = (eq / eq.cummax() - 1) * 100
    return {"return_pct": round(ret, 2), "cagr": round(cagr, 2), "max_dd": round(float(dd.min()), 2)}


def price_path_diff(binance_4h: pd.DataFrame, hyper_4h: pd.DataFrame) -> Dict[str, float]:
    merged = pd.concat(
        [binance_4h[["close"]].rename(columns={"close": "binance_close"}), hyper_4h[["close"]].rename(columns={"close": "hyper_close"})],
        axis=1,
        join="inner",
    ).dropna()
    b_ret = merged["binance_close"].pct_change().dropna()
    h_ret = merged["hyper_close"].pct_change().dropna()
    aligned = pd.concat([b_ret.rename("binance_ret"), h_ret.rename("hyper_ret")], axis=1).dropna()
    spread_bps = ((merged["hyper_close"] / merged["binance_close"] - 1).abs() * 10000)
    ret_diff_bps = ((aligned["hyper_ret"] - aligned["binance_ret"]).abs() * 10000)
    return {
        "bars_compared": int(len(aligned)),
        "return_correlation": round(float(aligned["binance_ret"].corr(aligned["hyper_ret"])), 6),
        "median_abs_close_spread_bps": round(float(spread_bps.median()), 3),
        "p95_abs_close_spread_bps": round(float(spread_bps.quantile(0.95)), 3),
        "median_abs_return_diff_bps": round(float(ret_diff_bps.median()), 3),
        "p95_abs_return_diff_bps": round(float(ret_diff_bps.quantile(0.95)), 3),
    }


def run_case(name: str, df_4h: pd.DataFrame, df_daily: pd.DataFrame, cfg: BacktestConfig, out_dir: Path) -> Dict[str, object]:
    features = build_features(df_4h, df_daily, cfg)
    trades, eq = Engine(cfg).run(features)
    summary = metrics(trades, eq, cfg.initial_capital)
    yearly = yearly_returns(eq, cfg.initial_capital)
    trades.to_csv(out_dir / f"{name}_trades.csv", index=False)
    eq.to_csv(out_dir / f"{name}_equity.csv")
    yearly.to_csv(out_dir / f"{name}_yearly.csv", index=False)
    pd.DataFrame([summary]).to_csv(out_dir / f"{name}_summary.csv", index=False)
    return {"summary": summary, "yearly": yearly, "trades": trades, "equity": eq}


def main() -> None:
    out_dir = Path("docs/backtests/v40_hyperliquid_validation")
    out_dir.mkdir(parents=True, exist_ok=True)

    end_ms = now_floor_4h_ms() - 1
    earliest_hl_4h = infer_earliest_hyperliquid_4h("BTC")
    start_hl_4h_ms = int(earliest_hl_4h.timestamp() * 1000)
    start_daily_warmup_ms = dt_to_ms("2021-01-01")

    hyper_4h = fetch_hyperliquid_klines("BTC", "4h", start_hl_4h_ms, end_ms, chunk_days=120)
    hyper_1d = fetch_hyperliquid_klines("BTC", "1d", start_daily_warmup_ms, end_ms, chunk_days=365)

    reliable_start, reliable_end, reliable_bars = longest_continuous_segment(hyper_4h, "4h")
    overlap_start = reliable_start
    overlap_end = reliable_end
    binance_4h = fetch_binance_klines("BTCUSDT", "4h", int(overlap_start.timestamp() * 1000), int(overlap_end.timestamp() * 1000))
    binance_1d = fetch_binance_klines("BTCUSDT", "1d", start_daily_warmup_ms, int(overlap_end.timestamp() * 1000))

    hyper_4h_overlap = hyper_4h.loc[(hyper_4h.index >= overlap_start) & (hyper_4h.index <= overlap_end)].copy()
    hyper_1d_overlap = hyper_1d.loc[hyper_1d.index <= overlap_end].copy()

    cfg_hl = BacktestConfig(name="hyperliquid", fee_rate=0.00045)
    cfg_hl_binance_fee = BacktestConfig(name="hyperliquid_binance_fee", fee_rate=0.0004)
    cfg_bin = BacktestConfig(name="binance_overlap", fee_rate=0.0004)

    hl_full = run_case("hyperliquid_full", hyper_4h, hyper_1d, cfg_hl, out_dir)
    hl_overlap = run_case("hyperliquid_overlap", hyper_4h_overlap, hyper_1d_overlap, cfg_hl, out_dir)
    hl_overlap_binance_fee = run_case("hyperliquid_overlap_binance_fee", hyper_4h_overlap, hyper_1d_overlap, cfg_hl_binance_fee, out_dir)
    bin_overlap = run_case("binance_overlap", binance_4h, binance_1d, cfg_bin, out_dir)

    comparison = pd.DataFrame([
        {"venue": "Hyperliquid", **hl_overlap["summary"]},
        {"venue": "Binance", **bin_overlap["summary"]},
    ])
    comparison.to_csv(out_dir / "comparison_overlap_summary.csv", index=False)

    yearly_cmp = (
        hl_overlap["yearly"].rename(columns={"return_pct": "hyperliquid_return_pct"})
        .merge(bin_overlap["yearly"].rename(columns={"return_pct": "binance_return_pct"}), on="year", how="outer")
        .sort_values("year")
    )
    yearly_cmp.to_csv(out_dir / "comparison_overlap_yearly.csv", index=False)

    diagnostics = {
        "generated_at_utc": pd.Timestamp.now(tz="UTC").isoformat(),
        "assumptions": {
            "strategy": "v40 exact repo logic: 4h entries/exits on bar close, daily EMA200 regime filter, EMA50/EMA200 trend, ATR14x3 stop, 2% risk",
            "fees": {"hyperliquid_taker": 0.00045, "binance_taker": 0.0004},
            "slippage": 0.0,
            "funding": 0.0,
            "borrow": 0.0,
        },
        "date_ranges": {
            "hyperliquid_4h": missing_bar_stats(hyper_4h, "4h"),
            "hyperliquid_1d": missing_bar_stats(hyper_1d, "1D"),
            "binance_4h_overlap": missing_bar_stats(binance_4h, "4h"),
            "binance_1d_overlap": missing_bar_stats(binance_1d, "1D"),
            "reliable_continuous_4h_segment": {
                "start": reliable_start.isoformat(),
                "end": reliable_end.isoformat(),
                "bars": reliable_bars,
            },
            "overlap_start": overlap_start.isoformat(),
            "overlap_end": overlap_end.isoformat(),
        },
        "buy_hold_overlap": {
            "hyperliquid": buy_hold_metrics(hyper_4h_overlap),
            "binance": buy_hold_metrics(binance_4h),
        },
        "price_path_diff": price_path_diff(binance_4h, hyper_4h_overlap),
        "fee_impact_on_hyperliquid_overlap": {
            "hyperliquid_fee_45bps_per_side": hl_overlap["summary"],
            "binance_fee_40bps_per_side": hl_overlap_binance_fee["summary"],
        },
    }
    (out_dir / "validation_diagnostics.json").write_text(json.dumps(diagnostics, indent=2))

    summary = {
        "hyperliquid_full": hl_full["summary"],
        "hyperliquid_overlap": hl_overlap["summary"],
        "binance_overlap": bin_overlap["summary"],
        "buy_hold_overlap": diagnostics["buy_hold_overlap"],
    }
    (out_dir / "validation_summary.json").write_text(json.dumps(summary, indent=2))

    print(json.dumps(summary, indent=2))
    print(f"\nFiles written to: {out_dir}")


if __name__ == "__main__":
    main()
