#!/usr/bin/env python3
"""
Backtest v40 — v37 + Riesgo 2% (BINANCE Futures BTCUSDT)

Lógica:
- Idéntica a v37 (Long-Short Trend 4h + Filtro de Régimen Diario EMA200)
- Cambio: riesgo por trade = 2% del equity
- Costos: comisión 0.04% por lado, slippage 0%
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from binance_futures_v37_backtest import (
    BacktestConfig,
    Engine,
    build_features,
    fetch_futures_klines,
    metrics,
    yearly_table,
)


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
    p.add_argument("--output-dir", default="docs/backtests/v40_long_short_trend_4h_daily_ema200_filter_risk_2pct")
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

    trades.to_csv(out_dir / "v40_trades.csv", index=False)
    eq.to_csv(out_dir / "v40_equity.csv")
    y.to_csv(out_dir / "v40_yearly.csv", index=False)
    pd.DataFrame([m]).to_csv(out_dir / "v40_summary.csv", index=False)

    print("\n=== v40 (v37 + Riesgo 2%) ===")
    print(pd.DataFrame([m]).to_string(index=False))
    print("\nRetornos anuales (%):")
    print(y.to_string(index=False))
    print(f"\nArchivos en: {out_dir}")


if __name__ == "__main__":
    main()
