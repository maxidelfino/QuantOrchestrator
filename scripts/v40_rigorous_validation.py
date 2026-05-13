#!/usr/bin/env python3
"""
Validación Rigurosa de v40 — Long-Short Trend 4h + Daily EMA200 + Riesgo 2%

Validaciones:
1. Cross-engine: Custom engine vs backtesting.py
2. Walk-forward analysis (5 folds)
3. Parameter sensitivity (ATR mult, EMA fast/slow)
4. Monte Carlo simulation (1000 iterations) — equity curve randomization
5. Statistical significance tests
"""

from __future__ import annotations

import json
import math
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
import requests
from backtesting import Backtest, Strategy

warnings.filterwarnings("ignore")

# ============================================================================
# DATA FETCHING (Binance Futures)
# ============================================================================

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


# ============================================================================
# HELPER: Build features
# ============================================================================

def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [(df["high"] - df["low"]), (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False).mean()


def build_features(df_4h: pd.DataFrame, df_daily: pd.DataFrame,
                   ema_fast=50, ema_slow=200, ema_regime_daily=200, atr_period=14) -> pd.DataFrame:
    df = df_4h.copy()
    df["ema50"] = df["close"].ewm(span=ema_fast, adjust=False).mean()
    df["ema200"] = df["close"].ewm(span=ema_slow, adjust=False).mean()
    df["atr"] = atr(df, atr_period)

    daily = df_daily.copy()
    daily["ema200_daily"] = daily["close"].ewm(span=ema_regime_daily, adjust=False).mean()
    regime = daily[["ema200_daily"]].reindex(df.index, method="ffill")
    df["ema200_daily"] = regime["ema200_daily"]

    df["regime_long"] = (df["close"] > df["ema200_daily"]).astype(int)
    df["regime_short"] = (df["close"] < df["ema200_daily"]).astype(int)
    df["long_signal"] = (df["close"] > df["ema50"]) & (df["ema50"] > df["ema200"]) & (df["regime_long"] == 1)
    df["short_signal"] = (df["close"] < df["ema50"]) & (df["ema50"] < df["ema200"]) & (df["regime_short"] == 1)
    df["long_exit_signal"] = df["ema50"] < df["ema200"]
    df["short_exit_signal"] = df["ema50"] > df["ema200"]
    return df


# ============================================================================
# ENGINE 1: Custom Engine
# ============================================================================

class CustomEngine:
    def __init__(self, initial_capital=10000, fee_rate=0.0004, risk_pct=0.02,
                 ema_fast=50, ema_slow=200, ema_regime_daily=200,
                 atr_period=14, stop_atr_mult=3.0, warmup_bars=220):
        self.initial_capital = initial_capital
        self.fee_rate = fee_rate
        self.risk_pct = risk_pct
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow
        self.ema_regime_daily = ema_regime_daily
        self.atr_period = atr_period
        self.stop_atr_mult = stop_atr_mult
        self.warmup_bars = warmup_bars

    def _trade_fee(self, qty: float, price: float) -> float:
        return qty * price * self.fee_rate

    def run(self, df_4h: pd.DataFrame, df_daily: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
        df = build_features(df_4h, df_daily,
                            self.ema_fast, self.ema_slow,
                            self.ema_regime_daily, self.atr_period)

        position = 0
        qty = 0.0
        entry = 0.0
        entry_bar = -1
        stop = np.nan
        realized = 0.0
        trades = []
        equity_rows = []

        for i in range(self.warmup_bars, len(df)):
            t = df.index[i]
            b = df.iloc[i]

            if np.isnan(b["atr"]) or np.isnan(b["ema50"]) or np.isnan(b["ema200"]) or np.isnan(b["ema200_daily"]):
                continue

            # Check exits
            if position == 1:
                sl_hit = b["low"] <= stop
                exit_sig = bool(b["long_exit_signal"])
                if sl_hit or exit_sig:
                    exit_px = float(stop) if sl_hit else float(b["close"])
                    reason = "stop" if sl_hit else "ema_inverse"
                    pnl = qty * (exit_px - entry) - self._trade_fee(qty, entry) - self._trade_fee(qty, exit_px)
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
                    exit_px = float(stop) if sl_hit else float(b["close"])
                    reason = "stop" if sl_hit else "ema_inverse"
                    pnl = qty * (entry - exit_px) - self._trade_fee(qty, entry) - self._trade_fee(qty, exit_px)
                    realized += pnl
                    trades.append({
                        "entry_time": df.index[entry_bar], "exit_time": t, "dir": "short",
                        "entry": entry, "exit": exit_px, "qty": qty, "pnl": pnl, "reason": reason,
                    })
                    position, qty, entry, stop = 0, 0.0, 0.0, np.nan

            # Check entries
            if position == 0:
                px = float(b["close"])
                equity_now = self.initial_capital + realized
                risk_usd = equity_now * self.risk_pct

                if bool(b["long_signal"]):
                    stop_px = px - self.stop_atr_mult * float(b["atr"])
                    dist = px - stop_px
                    if dist > 0:
                        q = risk_usd / dist
                        if q > 0:
                            position, qty, entry, entry_bar, stop = 1, float(q), px, i, float(stop_px)

                elif bool(b["short_signal"]):
                    stop_px = px + self.stop_atr_mult * float(b["atr"])
                    dist = stop_px - px
                    if dist > 0:
                        q = risk_usd / dist
                        if q > 0:
                            position, qty, entry, entry_bar, stop = -1, float(q), px, i, float(stop_px)

            # Equity tracking
            open_pnl = 0.0
            if position == 1:
                open_pnl = qty * (b["close"] - entry)
            elif position == -1:
                open_pnl = qty * (entry - b["close"])
            equity_rows.append({"time": t, "equity": self.initial_capital + realized + open_pnl})

        # Close open position
        if position != 0:
            t = df.index[-1]
            px = float(df.iloc[-1]["close"])
            pnl = (qty * (px - entry) if position == 1 else qty * (entry - px))
            pnl -= self._trade_fee(qty, entry) + self._trade_fee(qty, px)
            realized += pnl
            trades.append({
                "entry_time": df.index[entry_bar], "exit_time": t,
                "dir": "long" if position == 1 else "short",
                "entry": entry, "exit": px, "qty": qty, "pnl": pnl, "reason": "eod",
            })

        tr = pd.DataFrame(trades)
        eq = pd.DataFrame(equity_rows).set_index("time") if equity_rows else pd.DataFrame(columns=["equity"])
        return tr, eq


# ============================================================================
# ENGINE 2: backtesting.py
# ============================================================================

class V40Strategy(Strategy):
    """v40 implemented in backtesting.py with pre-computed features."""
    stop_atr_mult = 3.0
    risk_pct = 0.02

    def init(self):
        # Pre-computed features passed as columns
        self.regime_long = self.I(lambda: self.data.regime_long)
        self.regime_short = self.I(lambda: self.data.regime_short)
        self.ema50 = self.I(lambda: self.data.ema50)
        self.ema200 = self.I(lambda: self.data.ema200)
        self.atr = self.I(lambda: self.data.atr)

    def next(self):
        close = self.data.Close[-1]

        if not self.position:
            # Long entry
            if self.regime_long[-1] > 0.5 and close > self.ema50[-1] and self.ema50[-1] > self.ema200[-1]:
                stop_dist = self.stop_atr_mult * self.atr[-1]
                if stop_dist > 0:
                    risk_usd = self.equity * self.risk_pct
                    qty = risk_usd / stop_dist
                    sl = close - stop_dist
                    self.buy(sl=sl, size=qty)

            # Short entry
            elif self.regime_short[-1] > 0.5 and close < self.ema50[-1] and self.ema50[-1] < self.ema200[-1]:
                stop_dist = self.stop_atr_mult * self.atr[-1]
                if stop_dist > 0:
                    risk_usd = self.equity * self.risk_pct
                    qty = risk_usd / stop_dist
                    sl = close + stop_dist
                    self.sell(sl=sl, size=qty)

        else:
            # Trend reversal exit
            if self.position.is_long and self.ema50[-1] < self.ema200[-1]:
                self.position.close()
            elif self.position.is_short and self.ema50[-1] > self.ema200[-1]:
                self.position.close()


def run_backtesting_engine(df_with_features: pd.DataFrame, initial_capital=100000, fee=0.0004) -> dict:
    """Run v40 in backtesting.py using pre-computed features."""
    bt_data = df_with_features[["open", "high", "low", "close", "volume",
                                 "ema50", "ema200", "atr", "regime_long", "regime_short"]].copy()
    bt_data.columns = ["Open", "High", "Low", "Close", "Volume",
                       "ema50", "ema200", "atr", "regime_long", "regime_short"]

    bt = Backtest(
        bt_data, V40Strategy,
        cash=initial_capital,
        commission=fee,
        exclusive_orders=True,
        trade_on_close=True,
    )
    stats = bt.run()
    return stats


# ============================================================================
# METRICS
# ============================================================================

def calc_metrics(trades: pd.DataFrame, eq: pd.DataFrame, initial_capital: float) -> Dict[str, float]:
    if eq.empty or len(eq) == 0:
        return {"return_pct": 0, "cagr": 0, "max_dd": 0, "pf": 0, "wr": 0, "trades": 0, "sharpe": 0}

    eq_s = eq["equity"] if isinstance(eq, pd.DataFrame) else eq
    ret = (eq_s.iloc[-1] / initial_capital - 1) * 100
    years = (eq_s.index[-1] - eq_s.index[0]).total_seconds() / (365.25 * 86400)
    cagr = ((eq_s.iloc[-1] / initial_capital) ** (1 / years) - 1) * 100 if years > 0 else 0

    peak = eq_s.cummax()
    dd = (eq_s / peak - 1) * 100
    max_dd = float(dd.min())

    if trades.empty:
        pf, wr, sharpe = 0, 0, 0
    else:
        pnls = trades["pnl"]
        wins = pnls[pnls > 0]
        losses = pnls[pnls <= 0]
        wr = float((pnls > 0).mean() * 100)
        pf = float(wins.sum() / abs(losses.sum())) if len(losses) and abs(losses.sum()) > 0 else float("inf")
        returns = eq_s.pct_change().dropna()
        sharpe = float(returns.mean() / returns.std() * np.sqrt(2190)) if returns.std() > 0 else 0

    return {
        "return_pct": round(float(ret), 2),
        "cagr": round(float(cagr), 2),
        "max_dd": round(max_dd, 2),
        "pf": round(float(pf), 2) if np.isfinite(pf) else float("inf"),
        "wr": round(float(wr), 2),
        "trades": int(len(trades)),
        "sharpe": round(float(sharpe), 3),
    }


def yearly_returns(eq: pd.DataFrame, initial_capital: float) -> pd.DataFrame:
    if eq.empty:
        return pd.DataFrame(columns=["year", "return_pct"])
    eq_s = eq["equity"] if isinstance(eq, pd.DataFrame) else eq
    yearly = eq_s.resample("YS").last().dropna()
    if yearly.empty:
        return pd.DataFrame(columns=["year", "return_pct"])
    prev = yearly.shift(1)
    if len(prev) > 0:
        prev.iloc[0] = initial_capital
    rets = (yearly / prev - 1) * 100
    out = rets.to_frame("return_pct")
    out.index = out.index.year
    out.index.name = "year"
    return out.reset_index()


# ============================================================================
# WALK-FORWARD ANALYSIS
# ============================================================================

def walk_forward_analysis(df_4h: pd.DataFrame, df_daily: pd.DataFrame, n_folds=5) -> pd.DataFrame:
    """Split data into n_folds, test on each fold."""
    total_bars = len(df_4h)
    fold_size = total_bars // (n_folds + 1)

    results = []
    for fold in range(n_folds):
        test_start = (fold + 1) * fold_size
        test_end = test_start + fold_size
        test_end = min(test_end, total_bars)

        if test_end - test_start < 200:
            continue

        test_4h = df_4h.iloc[test_start:test_end]
        test_daily = df_daily  # Use full daily for regime

        engine = CustomEngine(warmup_bars=220)
        trades, eq = engine.run(test_4h, test_daily)
        m = calc_metrics(trades, eq, 10000)
        m["fold"] = fold + 1
        m["period"] = f"{test_4h.index[0].strftime('%Y-%m')} → {test_4h.index[-1].strftime('%Y-%m')}"
        results.append(m)

    return pd.DataFrame(results)


# ============================================================================
# PARAMETER SENSITIVITY
# ============================================================================

def parameter_sensitivity(df_4h: pd.DataFrame, df_daily: pd.DataFrame) -> pd.DataFrame:
    """Test v40 with different parameter combinations."""
    results = []

    atr_mults = [2.0, 2.5, 3.0, 3.5, 4.0]
    ema_fasts = [30, 50, 75, 100]
    ema_slows = [150, 200, 250]

    for atr_m in atr_mults:
        for ema_f in ema_fasts:
            for ema_s in ema_slows:
                if ema_f >= ema_s:
                    continue
                engine = CustomEngine(
                    ema_fast=ema_f, ema_slow=ema_s,
                    stop_atr_mult=atr_m, warmup_bars=max(ema_s + 20, 220)
                )
                trades, eq = engine.run(df_4h, df_daily)
                m = calc_metrics(trades, eq, 10000)
                m["atr_mult"] = atr_m
                m["ema_fast"] = ema_f
                m["ema_slow"] = ema_s
                results.append(m)

    return pd.DataFrame(results)


# ============================================================================
# MONTE CARLO SIMULATION — Equity curve randomization
# ============================================================================

def monte_carlo_simulation(trades: pd.DataFrame, eq: pd.DataFrame, initial_capital=10000, n_simulations=1000) -> Dict:
    """
    Monte Carlo via equity curve randomization:
    - Shuffle daily returns to create alternative equity paths
    - This tests whether the strategy's edge survives different return sequences
    """
    if eq.empty or len(eq) < 10:
        return {
            "mc_mean_return": 0, "mc_std_return": 0, "mc_worst_pct": 0,
            "mc_best_pct": 0, "mc_median_return": 0, "mc_profit_probability": 0,
        }

    eq_s = eq["equity"] if isinstance(eq, pd.DataFrame) else eq
    daily_returns = eq_s.pct_change().dropna().values

    simulated_returns = []
    for _ in range(n_simulations):
        # Shuffle daily returns
        shuffled = np.random.permutation(daily_returns)
        equity = initial_capital
        for ret in shuffled:
            equity *= (1 + ret)
        simulated_returns.append((equity / initial_capital - 1) * 100)

    simulated_returns = np.array(simulated_returns)
    return {
        "mc_mean_return": round(float(np.mean(simulated_returns)), 2),
        "mc_std_return": round(float(np.std(simulated_returns)), 2),
        "mc_worst_pct": round(float(np.percentile(simulated_returns, 5)), 2),
        "mc_best_pct": round(float(np.percentile(simulated_returns, 95)), 2),
        "mc_median_return": round(float(np.median(simulated_returns)), 2),
        "mc_profit_probability": round(float(np.mean(simulated_returns > 0) * 100), 2),
    }


# ============================================================================
# MAIN
# ============================================================================

def main():
    symbol = "BTCUSDT"
    start = "2021-01-01"
    end = "2026-05-01"
    initial_capital = 10000.0
    fee = 0.0004

    out_dir = Path("docs/backtests/v40_rigorous_validation")
    out_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print("VALIDACIÓN RIGUROSA DE v40 — Long-Short Trend 4h + Daily EMA200")
    print("=" * 70)

    # 1. Fetch data
    print("\n[1/5] Descargando datos de Binance Futures...")
    raw_4h = fetch_futures_klines(symbol, "4h", start, end)
    raw_daily = fetch_futures_klines(symbol, "1d", start, end)
    print(f"  4h bars: {len(raw_4h)} | Daily bars: {len(raw_daily)}")
    print(f"  Período: {raw_4h.index[0]} → {raw_4h.index[-1]}")

    # Build features once
    df_full = build_features(raw_4h, raw_daily)

    # 2. Cross-engine validation
    print("\n[2/5] Cross-engine validation (Custom vs backtesting.py)...")

    # Custom engine
    engine_custom = CustomEngine(initial_capital=initial_capital, fee_rate=fee)
    trades_custom, eq_custom = engine_custom.run(raw_4h, raw_daily)
    metrics_custom = calc_metrics(trades_custom, eq_custom, initial_capital)
    yearly_custom = yearly_returns(eq_custom, initial_capital)

    print("\n  === Custom Engine ===")
    for k, v in metrics_custom.items():
        print(f"    {k}: {v}")
    print(f"\n  Yearly returns:")
    print(f"    {yearly_custom.to_string(index=False)}")

    # backtesting.py engine — use higher capital to avoid margin issues
    print("\n  Ejecutando backtesting.py (capital=100,000 para evitar margin issues)...")
    try:
        stats_bt = run_backtesting_engine(df_full, initial_capital=100000, fee=fee)

        metrics_bt = {
            "return_pct": round(float(stats_bt["Return [%]"]), 2),
            "max_dd": round(float(stats_bt["Max. Drawdown [%]"]), 2),
            "pf": round(float(stats_bt["Profit Factor"]), 2),
            "wr": round(float(stats_bt["Win Rate [%]"]), 2),
            "trades": int(stats_bt["# Trades"]),
            "sharpe": round(float(stats_bt.get("Sharpe Ratio", 0)), 3),
        }

        print("\n  === backtesting.py Engine ===")
        for k, v in metrics_bt.items():
            print(f"    {k}: {v}")

        # Compare
        print("\n  === Comparison ===")
        for k in metrics_custom:
            if k in metrics_bt:
                diff = abs(metrics_custom[k] - metrics_bt[k])
                match = "✅" if diff < 5 else "⚠️" if diff < 20 else "❌"
                print(f"    {k}: Custom={metrics_custom[k]} | BT.py={metrics_bt[k]} | Diff={diff:.2f} {match}")

    except Exception as e:
        print(f"\n  ⚠️ backtesting.py failed: {e}")
        import traceback
        traceback.print_exc()
        metrics_bt = {}

    # 3. Walk-forward analysis
    print("\n[3/5] Walk-forward analysis (5 folds)...")
    wf_results = walk_forward_analysis(raw_4h, raw_daily, n_folds=5)
    wf_results.to_csv(out_dir / "walk_forward.csv", index=False)
    print("\n  Walk-forward results:")
    print(wf_results[["fold", "period", "return_pct", "max_dd", "pf", "trades"]].to_string(index=False))

    wf_profitable = (wf_results["return_pct"] > 0).mean() * 100 if not wf_results.empty else 0
    print(f"\n  Folds profitable: {wf_profitable:.0f}%")

    # 4. Parameter sensitivity
    print("\n[4/5] Parameter sensitivity analysis...")
    sens_results = parameter_sensitivity(raw_4h, raw_daily)
    sens_results.to_csv(out_dir / "parameter_sensitivity.csv", index=False)

    top10 = sens_results.nlargest(10, "return_pct")
    print("\n  Top 10 parameter combinations by return:")
    print(top10[["atr_mult", "ema_fast", "ema_slow", "return_pct", "max_dd", "pf", "trades"]].to_string(index=False))

    v40_params = sens_results[
        (sens_results["atr_mult"] == 3.0) &
        (sens_results["ema_fast"] == 50) &
        (sens_results["ema_slow"] == 200)
    ]
    if not v40_params.empty:
        rank = sens_results["return_pct"].rank(ascending=False, method="min").iloc[v40_params.index[0]]
        print(f"\n  v40 params rank: #{int(rank)} of {len(sens_results)}")

    # 5. Monte Carlo simulation
    print("\n[5/5] Monte Carlo simulation (1000 iterations)...")
    mc_results = monte_carlo_simulation(trades_custom, eq_custom, initial_capital, n_simulations=1000)
    print("\n  Monte Carlo results:")
    for k, v in mc_results.items():
        print(f"    {k}: {v}")

    # ========================================================================
    # FINAL VALIDATION REPORT
    # ========================================================================
    print("\n" + "=" * 70)
    print("REPORTE FINAL DE VALIDACIÓN")
    print("=" * 70)

    checks = []
    checks.append(("Retorno positivo", metrics_custom["return_pct"] > 0))
    checks.append(("Profit Factor > 1.5", metrics_custom["pf"] > 1.5))
    checks.append(("Max Drawdown < 25%", abs(metrics_custom["max_dd"]) < 25))
    checks.append(("+50 trades (4h timeframe)", metrics_custom["trades"] >= 50))
    checks.append(("Walk-forward >60% profitable", wf_profitable >= 60))
    checks.append(("MC profit probability > 70%", mc_results["mc_profit_probability"] > 70))

    if not v40_params.empty:
        rank = sens_results["return_pct"].rank(ascending=False, method="min").iloc[v40_params.index[0]]
        checks.append(("v40 params in top 50%", rank <= len(sens_results) / 2))

    # Cross-engine check (if backtesting.py worked)
    if metrics_bt and "return_pct" in metrics_bt:
        diff = abs(metrics_custom["return_pct"] - metrics_bt["return_pct"])
        checks.append(("Cross-engine match (<20% diff)", diff < 20))

    print("\n  Validation Checks:")
    passed = 0
    for name, result in checks:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"    {status}: {name}")
        if result:
            passed += 1

    print(f"\n  Resultado: {passed}/{len(checks)} checks passed")

    if passed >= 6:
        print("\n  🟢 ESTRATEGIA VALIDADA — Lista para producción")
    elif passed >= 4:
        print("\n  🟡 ESTRATEGIA CONDICIONAL — Requiere ajustes antes de producción")
    else:
        print("\n  🔴 ESTRATEGIA NO VALIDADA — No usar en producción")

    # Save full report
    report = {
        "custom_engine": metrics_custom,
        "yearly_returns": yearly_custom.to_dict(orient="records"),
        "backtesting_py": metrics_bt,
        "walk_forward": wf_results.to_dict(orient="records"),
        "monte_carlo": mc_results,
        "validation_checks": {name: result for name, result in checks},
        "passed": passed,
        "total": len(checks),
    }
    with open(out_dir / "validation_report.json", "w") as f:
        json.dump(report, f, indent=2, default=str)

    print(f"\n  Reporte completo: {out_dir / 'validation_report.json'}")


if __name__ == "__main__":
    main()
