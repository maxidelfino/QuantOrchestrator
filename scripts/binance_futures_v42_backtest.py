#!/usr/bin/env python3
"""
Backtest v42 — v40 + Filtro ADX para mercados laterales

Lógica:
- Idéntica a v40 (Long-Short Trend 4h + Filtro de Régimen Diario EMA200)
- NUEVO: ADX(14) > 25 requerido para entrar (evita rangos laterales)
- Costos: comisión 0.04% por lado, slippage 0%
- Riesgo: 2% del equity por trade
"""

from __future__ import annotations

import argparse
import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
import requests


BINANCE_FAPI_KLINES = "https://fapi.binance.com/fapi/v1/klines"


def dt_to_ms(s: str) -> int:
    return int(datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)


def fetch_futures_klines(symbol: str, interval: str, start_date: str, end_date: str) -> pd.DataFrame:
    start_ms = dt_to_ms(start_date)
    end_ms = dt_to_ms(end_date) + 24 * 60 * 60 * 1000
    out: List[list] = []
    cur = start_ms

    while cur < end_ms:
        params = {"symbol": symbol, "interval": interval, "startTime": cur, "limit": 1500}
        r = requests.get(BINANCE_FAPI_KLINES, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        if not data:
            break
        out.extend(data)
        last_open = data[-1][0]
        if len(data) < 1500:
            break
        cur = last_open + 1
        time.sleep(0.05)

    if not out:
        raise RuntimeError(f"No se pudieron descargar klines futures ({interval})")

    df = pd.DataFrame(
        out,
        columns=[
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "trades",
            "taker_buy_base", "taker_buy_quote", "ignore",
        ],
    )
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = df[c].astype(float)
    df = df.set_index("open_time")[["open", "high", "low", "close", "volume"]].sort_index()
    df = df[~df.index.duplicated(keep="last")]
    return df


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [(df["high"] - df["low"]), (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False).mean()


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Calculate ADX (Average Directional Index)"""
    high = df["high"]
    low = df["low"]
    close = df["close"]

    # True Range
    prev_close = close.shift(1)
    tr = pd.concat([
        (high - low),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    # Directional Movement
    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

    # Smoothed TR and DM
    atr_smooth = tr.ewm(alpha=1.0 / period, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1.0 / period, adjust=False).mean() / atr_smooth
    minus_di = 100 * minus_dm.ewm(alpha=1.0 / period, adjust=False).mean() / atr_smooth

    # DX and ADX
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    adx = dx.ewm(alpha=1.0 / period, adjust=False).mean()
    return adx


@dataclass
class BacktestConfig:
    symbol: str = "BTCUSDT"
    interval_4h: str = "4h"
    interval_daily: str = "1d"
    start_date: str = "2021-01-01"
    end_date: str = "2026-05-01"
    initial_capital: float = 10_000.0
    fee_rate: float = 0.0004
    slippage_rate: float = 0.0
    risk_pct: float = 0.02
    ema_fast: int = 50
    ema_slow: int = 200
    ema_regime_daily: int = 200
    atr_period: int = 14
    stop_atr_mult: float = 3.0
    adx_period: int = 14
    adx_threshold: float = 25.0  # ADX > 25 = tendencia fuerte
    warmup_bars: int = 220


def build_features(df_4h: pd.DataFrame, df_daily: pd.DataFrame, cfg: BacktestConfig) -> pd.DataFrame:
    out = df_4h.copy()
    out["ema50"] = out["close"].ewm(span=cfg.ema_fast, adjust=False).mean()
    out["ema200"] = out["close"].ewm(span=cfg.ema_slow, adjust=False).mean()
    out["atr"] = atr(out, cfg.atr_period)
    out["adx"] = adx(out, cfg.adx_period)

    daily = df_daily.copy()
    daily["ema200_daily"] = daily["close"].ewm(span=cfg.ema_regime_daily, adjust=False).mean()
    regime = daily[["ema200_daily"]].reindex(out.index, method="ffill")
    out["ema200_daily"] = regime["ema200_daily"]

    out["regime_long"] = out["close"] > out["ema200_daily"]
    out["regime_short"] = out["close"] < out["ema200_daily"]

    # NUEVO: Filtro ADX - solo operar si hay tendencia fuerte
    out["strong_trend"] = out["adx"] > cfg.adx_threshold

    base_long = (out["close"] > out["ema50"]) & (out["ema50"] > out["ema200"])
    base_short = (out["close"] < out["ema50"]) & (out["ema50"] < out["ema200"])

    out["long_signal"] = base_long & out["regime_long"] & out["strong_trend"]
    out["short_signal"] = base_short & out["regime_short"] & out["strong_trend"]
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
                    if sl_hit:
                        exit_px = float(stop)
                        reason = "stop"
                    else:
                        exit_px = float(b["close"])
                        reason = "ema_inverse"

                    pnl = qty * (exit_px - entry)
                    pnl -= self._trade_fee(qty, entry) + self._trade_fee(qty, exit_px)
                    realized += pnl
                    trades.append({
                        "entry_time": df.index[entry_bar], "exit_time": t, "dir": "long",
                        "entry": entry, "exit": exit_px, "qty": qty, "pnl": pnl, "reason": reason,
                    })
                    position, qty, entry, stop = 0, 0.0, 0.0, np.nan

            elif position == -1:
                sl_hit = b["high"] >= stop
                exit_sig = bool(b["short_exit_signal"])
                if sl_hit or exit_sig:
                    if sl_hit:
                        exit_px = float(stop)
                        reason = "stop"
                    else:
                        exit_px = float(b["close"])
                        reason = "ema_inverse"

                    pnl = qty * (entry - exit_px)
                    pnl -= self._trade_fee(qty, entry) + self._trade_fee(qty, exit_px)
                    realized += pnl
                    trades.append({
                        "entry_time": df.index[entry_bar], "exit_time": t, "dir": "short",
                        "entry": entry, "exit": exit_px, "qty": qty, "pnl": pnl, "reason": reason,
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
                            position = 1
                            qty = float(q)
                            entry = px
                            entry_bar = i
                            stop = float(stop_px)

                elif bool(b["short_signal"]):
                    stop_px = px + self.cfg.stop_atr_mult * float(b["atr"])
                    dist = stop_px - px
                    if dist > 0:
                        q = risk_usd / dist
                        if q > 0:
                            position = -1
                            qty = float(q)
                            entry = px
                            entry_bar = i
                            stop = float(stop_px)

            if position == 1:
                open_pnl = qty * (b["close"] - entry)
            elif position == -1:
                open_pnl = qty * (entry - b["close"])
            else:
                open_pnl = 0.0

            equity_rows.append({"time": t, "equity": self.cfg.initial_capital + realized + open_pnl})

        if position != 0:
            t = df.index[-1]
            px = float(df.iloc[-1]["close"])
            if position == 1:
                pnl = qty * (px - entry)
                direction = "long"
            else:
                pnl = qty * (entry - px)
                direction = "short"

            pnl -= self._trade_fee(qty, entry) + self._trade_fee(qty, px)
            realized += pnl
            trades.append({
                "entry_time": df.index[entry_bar], "exit_time": t,
                "dir": direction, "entry": entry, "exit": px, "qty": qty, "pnl": pnl, "reason": "eod",
            })

        tr = pd.DataFrame(trades)
        eq = pd.DataFrame(equity_rows).set_index("time") if equity_rows else pd.DataFrame(columns=["equity"])
        return tr, eq


def _period_returns_from_equity(eq: pd.Series, freq: str, initial_capital: float) -> pd.Series:
    e = eq.resample(freq).last().dropna()
    if e.empty:
        return pd.Series(dtype=float)
    prev = e.shift(1)
    prev.iloc[0] = initial_capital
    return (e / prev - 1) * 100


def metrics(trades: pd.DataFrame, eq: pd.DataFrame, initial_capital: float) -> Dict[str, float]:
    if eq.empty:
        return {k: 0.0 for k in ["return_pct", "cagr", "max_dd", "pf", "wr", "trades"]}

    eq_s = eq["equity"].dropna()
    ret = (eq_s.iloc[-1] / initial_capital - 1) * 100
    years = (eq_s.index[-1] - eq_s.index[0]).total_seconds() / (365.25 * 86400)
    cagr = ((eq_s.iloc[-1] / initial_capital) ** (1 / years) - 1) * 100 if years > 0 else 0.0

    peak = eq_s.cummax()
    dd = (eq_s / peak - 1) * 100
    max_dd = float(dd.min())

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
    }


def yearly_table(eq: pd.DataFrame, initial_capital: float) -> pd.DataFrame:
    if eq.empty:
        return pd.DataFrame(columns=["year", "return_pct"])
    y = _period_returns_from_equity(eq["equity"], "YS", initial_capital)
    out = y.to_frame("return_pct")
    out.index = out.index.year
    out.index.name = "year"
    return out.reset_index()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--start", default="2021-01-01")
    p.add_argument("--end", default="2026-05-01")
    p.add_argument("--interval-4h", default="4h")
    p.add_argument("--interval-daily", default="1d")
    p.add_argument("--initial-capital", type=float, default=10_000.0)
    p.add_argument("--fee", type=float, default=0.0004)
    p.add_argument("--slippage", type=float, default=0.0)
    p.add_argument("--adx-threshold", type=float, default=25.0)
    p.add_argument("--output-dir", default="docs/backtests/v42_adx_filter")
    args = p.parse_args()

    cfg = BacktestConfig(
        symbol=args.symbol,
        interval_4h=args.interval_4h,
        interval_daily=args.interval_daily,
        start_date=args.start,
        end_date=args.end,
        initial_capital=args.initial_capital,
        fee_rate=args.fee,
        slippage_rate=args.slippage,
        risk_pct=0.02,
        adx_threshold=args.adx_threshold,
    )

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Descargando {cfg.symbol} 4h Binance Futures: {cfg.start_date} → {cfg.end_date}")
    raw_4h = fetch_futures_klines(cfg.symbol, cfg.interval_4h, cfg.start_date, cfg.end_date)
    print(f"Descargando {cfg.symbol} diario Binance Futures: {cfg.start_date} → {cfg.end_date}")
    raw_daily = fetch_futures_klines(cfg.symbol, cfg.interval_daily, cfg.start_date, cfg.end_date)

    df = build_features(raw_4h, raw_daily, cfg)
    engine = Engine(cfg)
    trades, eq = engine.run(df)
    m = metrics(trades, eq, cfg.initial_capital)
    y = yearly_table(eq, cfg.initial_capital)

    trades.to_csv(out_dir / "v42_trades.csv", index=False)
    eq.to_csv(out_dir / "v42_equity.csv")
    y.to_csv(out_dir / "v42_yearly.csv", index=False)
    pd.DataFrame([m]).to_csv(out_dir / "v42_summary.csv", index=False)

    print("\n=== v42 (v40 + Filtro ADX > 25) ===")
    print(pd.DataFrame([m]).to_string(index=False))
    print("\nRetornos anuales (%):")
    print(y.to_string(index=False))
    print(f"\nArchivos en: {out_dir}")


if __name__ == "__main__":
    main()
