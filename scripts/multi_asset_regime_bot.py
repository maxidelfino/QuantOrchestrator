#!/usr/bin/env python3
"""
Multi-Asset Regime-Switching Bot v2

Arquitectura:
1. RegimeDetector → Detecta tendencia/rango para cada activo
2. AssetSelector → Elige el activo con mejor tendencia
3. MultiAssetEngine → Ejecuta v40 en el activo seleccionado

Fix v2: Maneja assets con diferentes fechas de inicio correctamente.
Usa el índice del activo primario como master, y solo considera otros
activos cuando tienen datos disponibles.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import requests

# ============================================================================
# DATA FETCHING
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
        raise RuntimeError(f"No se pudieron descargar klines futures ({symbol} {interval})")

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
# INDICATORS
# ============================================================================

def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        (df["high"] - df["low"]),
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False).mean()


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = df["high"]
    low = df["low"]
    close = df["close"]

    prev_close = close.shift(1)
    tr = pd.concat([
        (high - low),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

    atr_smooth = tr.ewm(alpha=1.0 / period, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1.0 / period, adjust=False).mean() / atr_smooth
    minus_di = 100 * minus_dm.ewm(alpha=1.0 / period, adjust=False).mean() / atr_smooth

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    return dx.ewm(alpha=1.0 / period, adjust=False).mean()


# ============================================================================
# REGIME DETECTOR
# ============================================================================

@dataclass
class RegimeState:
    symbol: str
    is_trending: bool
    trend_direction: str    # "long", "short", or "none"
    trend_strength: float   # 0-100
    adx_value: float
    ema50: float
    ema200: float
    ema200_daily: float
    close: float
    atr_value: float
    has_data: bool = True   # False if asset doesn't have enough data

    @property
    def is_long_regime(self) -> bool:
        return self.trend_direction == "long"

    @property
    def is_short_regime(self) -> bool:
        return self.trend_direction == "short"


class RegimeDetector:
    def __init__(self, ema_fast=50, ema_slow=200, ema_regime_daily=200,
                 atr_period=14, adx_period=14, adx_trend_threshold=20):
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow
        self.ema_regime_daily = ema_regime_daily
        self.atr_period = atr_period
        self.adx_period = adx_period
        self.adx_trend_threshold = adx_trend_threshold

    def detect(self, df_4h: pd.DataFrame, df_daily: pd.DataFrame, symbol: str) -> RegimeState:
        if len(df_4h) < self.ema_slow + 20:
            return RegimeState(
                symbol=symbol, is_trending=False, trend_direction="none",
                trend_strength=0, adx_value=0, ema50=0, ema200=0,
                ema200_daily=0, close=0, atr_value=0, has_data=False,
            )

        close = df_4h["close"].iloc[-1]
        ema50 = df_4h["close"].ewm(span=self.ema_fast, adjust=False).mean().iloc[-1]
        ema200 = df_4h["close"].ewm(span=self.ema_slow, adjust=False).mean().iloc[-1]
        atr_val = atr(df_4h, self.atr_period).iloc[-1]
        adx_val = adx(df_4h, self.adx_period).iloc[-1]

        daily_ema = df_daily["close"].ewm(span=self.ema_regime_daily, adjust=False).mean()
        ema200_daily = daily_ema.iloc[-1]

        daily_bull = close > ema200_daily
        daily_bear = close < ema200_daily
        ema_bull = ema50 > ema200
        ema_bear = ema50 < ema200

        adx_score = min(adx_val / 50 * 100, 100)
        ema_separation = abs(ema50 - ema200) / ema200 * 100
        ema_score = min(ema_separation * 10, 100)
        daily_aligned = (daily_bull and ema_bull) or (daily_bear and ema_bear)
        daily_bonus = 20 if daily_aligned else 0

        trend_strength = min((adx_score * 0.5 + ema_score * 0.3 + daily_bonus), 100)

        if daily_bull and ema_bull:
            direction = "long"
        elif daily_bear and ema_bear:
            direction = "short"
        else:
            direction = "none"

        is_trending = adx_val > self.adx_trend_threshold and direction != "none"

        return RegimeState(
            symbol=symbol, is_trending=is_trending, trend_direction=direction,
            trend_strength=trend_strength, adx_value=round(adx_val, 2),
            ema50=round(ema50, 2), ema200=round(ema200, 2),
            ema200_daily=round(ema200_daily, 2), close=round(close, 2),
            atr_value=round(atr_val, 2), has_data=True,
        )


# ============================================================================
# ASSET SELECTOR
# ============================================================================

class AssetSelector:
    def __init__(self, primary_symbol="BTCUSDT", min_trend_strength=15):
        self.primary_symbol = primary_symbol
        self.min_trend_strength = min_trend_strength

    def select(self, regimes: Dict[str, RegimeState]) -> Tuple[str, RegimeState]:
        primary = regimes.get(self.primary_symbol)
        if primary and primary.has_data and primary.is_trending and primary.trend_strength >= self.min_trend_strength:
            return self.primary_symbol, primary

        best_symbol = None
        best_strength = -1

        for symbol, regime in regimes.items():
            if symbol == self.primary_symbol:
                continue
            if regime.has_data and regime.is_trending and regime.trend_strength > best_strength:
                best_strength = regime.trend_strength
                best_symbol = symbol

        if best_symbol and best_strength >= self.min_trend_strength:
            return best_symbol, regimes[best_symbol]

        # Fallback to primary
        if primary and primary.has_data:
            return self.primary_symbol, primary

        # Last resort: any asset with data
        for symbol, regime in regimes.items():
            if regime.has_data:
                return symbol, regime

        return self.primary_symbol, RegimeState(
            self.primary_symbol, False, "none", 0, 0, 0, 0, 0, 0, 0, False,
        )


# ============================================================================
# MULTI-ASSET ENGINE
# ============================================================================

@dataclass
class BacktestConfig:
    symbols: List[str] = field(default_factory=lambda: ["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    primary_symbol: str = "BTCUSDT"
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
    warmup_bars: int = 220
    min_trend_strength: float = 15.0


class MultiAssetEngine:
    def __init__(self, cfg: BacktestConfig):
        self.cfg = cfg
        self.detector = RegimeDetector(
            ema_fast=cfg.ema_fast, ema_slow=cfg.ema_slow,
            ema_regime_daily=cfg.ema_regime_daily,
            atr_period=cfg.atr_period,
        )
        self.selector = AssetSelector(
            primary_symbol=cfg.primary_symbol,
            min_trend_strength=cfg.min_trend_strength,
        )

    def _trade_fee(self, qty: float, price: float) -> float:
        return qty * price * (self.cfg.fee_rate + self.cfg.slippage_rate)

    def _build_features(self, df_4h: pd.DataFrame, df_daily: pd.DataFrame) -> pd.DataFrame:
        """Build features for a single asset's 4h data."""
        df = df_4h.copy()
        df["ema50"] = df["close"].ewm(span=self.cfg.ema_fast, adjust=False).mean()
        df["ema200"] = df["close"].ewm(span=self.cfg.ema_slow, adjust=False).mean()
        df["atr"] = atr(df, self.cfg.atr_period)
        df["adx"] = adx(df, 14)

        daily_ema = df_daily["close"].ewm(span=self.cfg.ema_regime_daily, adjust=False).mean()
        df["ema200_daily"] = daily_ema.reindex(df.index, method="ffill")

        df["regime_long"] = df["close"] > df["ema200_daily"]
        df["regime_short"] = df["close"] < df["ema200_daily"]
        df["long_signal"] = (df["close"] > df["ema50"]) & (df["ema50"] > df["ema200"]) & df["regime_long"]
        df["short_signal"] = (df["close"] < df["ema50"]) & (df["ema50"] < df["ema200"]) & df["regime_short"]
        df["long_exit_signal"] = df["ema50"] < df["ema200"]
        df["short_exit_signal"] = df["ema50"] > df["ema200"]
        return df

    def run(self, data_4h: Dict[str, pd.DataFrame], data_daily: Dict[str, pd.DataFrame]) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        # Use primary symbol's index as master timeline
        primary_4h = data_4h[self.cfg.primary_symbol]
        master_index = primary_4h.index

        # Build features for all symbols (aligned to their own data)
        features = {}
        for symbol in self.cfg.symbols:
            if symbol not in data_4h or symbol not in data_daily:
                continue
            features[symbol] = self._build_features(data_4h[symbol], data_daily[symbol])

        # Trading state
        position = 0
        current_symbol = self.cfg.primary_symbol
        qty = 0.0
        entry = 0.0
        entry_idx = -1  # Index in master timeline
        stop = np.nan
        realized = 0.0

        trades = []
        equity_rows = []
        regime_log = []

        for i in range(self.cfg.warmup_bars, len(master_index)):
            t = master_index[i]

            # Detect regimes for all symbols (using data up to current time)
            regimes = {}
            for symbol in self.cfg.symbols:
                if symbol not in features:
                    continue
                feat = features[symbol]
                # Get data up to current time
                mask = feat.index <= t
                if mask.sum() < self.cfg.ema_slow + 20:
                    regimes[symbol] = RegimeState(
                        symbol, False, "none", 0, 0, 0, 0, 0, 0, 0, False,
                    )
                    continue
                df_4h_slice = feat[mask].iloc[:, :5]  # OHLCV only for detection
                regimes[symbol] = self.detector.detect(df_4h_slice, data_daily[symbol], symbol)

            # Select best asset
            selected_symbol, selected_regime = self.selector.select(regimes)

            # Log regime
            primary_regime = regimes.get(self.cfg.primary_symbol, RegimeState(self.cfg.primary_symbol, False, "none", 0, 0, 0, 0, 0, 0, 0, False))
            regime_log.append({
                "time": t,
                "selected_symbol": selected_symbol,
                "primary_trending": primary_regime.is_trending,
                "primary_strength": primary_regime.trend_strength,
                "selected_strength": selected_regime.trend_strength,
                "selected_direction": selected_regime.trend_direction,
            })

            # Get features for the asset we're currently trading (for exits)
            # and the selected asset (for potential new entries)
            active_feat = features.get(current_symbol) if position != 0 else None
            selected_feat = features.get(selected_symbol)

            # REGIME SWITCH: If we have a position and the selected asset changed,
            # close the position. We only hold positions when regime supports the asset.
            if position != 0 and selected_symbol != current_symbol:
                if active_feat is not None:
                    active_mask = active_feat.index <= t
                    if active_mask.sum() > 0:
                        b_active = active_feat[active_mask].iloc[-1]
                        exit_px = float(b_active["close"])
                        if position == 1:
                            pnl = qty * (exit_px - entry)
                        else:
                            pnl = qty * (entry - exit_px)
                        pnl -= self._trade_fee(qty, entry) + self._trade_fee(qty, exit_px)
                        realized += pnl
                        trades.append({
                            "entry_time": master_index[entry_idx], "exit_time": t,
                            "symbol": current_symbol, "dir": "long" if position == 1 else "short",
                            "entry": entry, "exit": exit_px, "qty": qty, "pnl": pnl,
                            "reason": "regime_switch",
                        })
                        position, qty, entry, stop = 0, 0.0, 0.0, np.nan
                        active_feat = None  # Position closed

            # Check exits using the ACTIVE asset's features
            if position != 0 and active_feat is not None:
                # Find the bar in active symbol's data closest to current time
                active_mask = active_feat.index <= t
                if active_mask.sum() > 0:
                    b_active = active_feat[active_mask].iloc[-1]

                    if position == 1:
                        sl_hit = b_active["low"] <= stop
                        exit_sig = bool(b_active["long_exit_signal"])
                        if sl_hit or exit_sig:
                            exit_px = float(stop) if sl_hit else float(b_active["close"])
                            reason = "stop" if sl_hit else "ema_inverse"
                            pnl = qty * (exit_px - entry) - self._trade_fee(qty, entry) - self._trade_fee(qty, exit_px)
                            realized += pnl
                            trades.append({
                                "entry_time": master_index[entry_idx], "exit_time": t,
                                "symbol": current_symbol, "dir": "long",
                                "entry": entry, "exit": exit_px, "qty": qty, "pnl": pnl, "reason": reason,
                            })
                            position, qty, entry, stop = 0, 0.0, 0.0, np.nan

                    elif position == -1:
                        sl_hit = b_active["high"] >= stop
                        exit_sig = bool(b_active["short_exit_signal"])
                        if sl_hit or exit_sig:
                            exit_px = float(stop) if sl_hit else float(b_active["close"])
                            reason = "stop" if sl_hit else "ema_inverse"
                            pnl = qty * (entry - exit_px) - self._trade_fee(qty, entry) - self._trade_fee(qty, exit_px)
                            realized += pnl
                            trades.append({
                                "entry_time": master_index[entry_idx], "exit_time": t,
                                "symbol": current_symbol, "dir": "short",
                                "entry": entry, "exit": exit_px, "qty": qty, "pnl": pnl, "reason": reason,
                            })
                            position, qty, entry, stop = 0, 0.0, 0.0, np.nan

            # Check entries (only if flat, using the SELECTED asset's features)
            if position == 0 and selected_feat is not None:
                sel_mask = selected_feat.index <= t
                if sel_mask.sum() > 0:
                    b = selected_feat[sel_mask].iloc[-1]
                    px = float(b["close"])
                    equity_now = self.cfg.initial_capital + realized
                    risk_usd = equity_now * self.cfg.risk_pct

                    if bool(b["long_signal"]):
                        stop_px = px - self.cfg.stop_atr_mult * float(b["atr"])
                        dist = px - stop_px
                        if dist > 0:
                            q = risk_usd / dist
                            if q > 0:
                                position, qty, entry, entry_idx, stop, current_symbol = 1, float(q), px, i, float(stop_px), selected_symbol

                    elif bool(b["short_signal"]):
                        stop_px = px + self.cfg.stop_atr_mult * float(b["atr"])
                        dist = stop_px - px
                        if dist > 0:
                            q = risk_usd / dist
                            if q > 0:
                                position, qty, entry, entry_idx, stop, current_symbol = -1, float(q), px, i, float(stop_px), selected_symbol

            # Equity tracking - use the active asset's close for open PnL
            if position != 0 and active_feat is not None:
                active_mask = active_feat.index <= t
                if active_mask.sum() > 0:
                    b_active = active_feat[active_mask].iloc[-1]
                    if position == 1:
                        open_pnl = qty * (b_active["close"] - entry)
                    elif position == -1:
                        open_pnl = qty * (entry - b_active["close"])
                    else:
                        open_pnl = 0.0
                else:
                    open_pnl = 0.0
            else:
                open_pnl = 0.0

            equity_rows.append({"time": t, "equity": self.cfg.initial_capital + realized + open_pnl})

        # Close open position at end
        if position != 0:
            feat = features.get(current_symbol)
            if feat is not None:
                t = feat.index[-1]
                px = float(feat.iloc[-1]["close"])
                pnl = (qty * (px - entry) if position == 1 else qty * (entry - px))
                pnl -= self._trade_fee(qty, entry) + self._trade_fee(qty, px)
                realized += pnl
                trades.append({
                    "entry_time": master_index[entry_idx], "exit_time": t,
                    "symbol": current_symbol,
                    "dir": "long" if position == 1 else "short",
                    "entry": entry, "exit": px, "qty": qty, "pnl": pnl, "reason": "eod",
                })

        tr = pd.DataFrame(trades)
        eq = pd.DataFrame(equity_rows).set_index("time") if equity_rows else pd.DataFrame(columns=["equity"])
        rl = pd.DataFrame(regime_log).set_index("time") if regime_log else pd.DataFrame()

        return tr, eq, rl


# ============================================================================
# METRICS
# ============================================================================

def calc_metrics(trades: pd.DataFrame, eq: pd.DataFrame, initial_capital: float) -> Dict[str, float]:
    if eq.empty:
        return {"return_pct": 0, "cagr": 0, "max_dd": 0, "pf": 0, "wr": 0, "trades": 0}

    eq_s = eq["equity"].dropna()
    ret = (eq_s.iloc[-1] / initial_capital - 1) * 100
    years = (eq_s.index[-1] - eq_s.index[0]).total_seconds() / (365.25 * 86400)
    cagr = ((eq_s.iloc[-1] / initial_capital) ** (1 / years) - 1) * 100 if years > 0 else 0.0

    peak = eq_s.cummax()
    dd = (eq_s / peak - 1) * 100
    max_dd = float(dd.min())

    if trades.empty:
        pf, wr = 0.0, 0.0
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
    eq_s = eq["equity"].dropna()
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
# MAIN
# ============================================================================

def main():
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT")
    p.add_argument("--primary", default="BTCUSDT")
    p.add_argument("--start", default="2021-01-01")
    p.add_argument("--end", default="2026-05-01")
    p.add_argument("--initial-capital", type=float, default=10_000.0)
    p.add_argument("--fee", type=float, default=0.0004)
    p.add_argument("--min-trend-strength", type=float, default=15.0)
    p.add_argument("--output-dir", default="docs/backtests/multi_asset_regime_switch_v2")
    args = p.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",")]
    cfg = BacktestConfig(
        symbols=symbols,
        primary_symbol=args.primary,
        start_date=args.start,
        end_date=args.end,
        initial_capital=args.initial_capital,
        fee_rate=args.fee,
        min_trend_strength=args.min_trend_strength,
    )

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print("MULTI-ASSET REGIME-SWITCHING BACKTEST v2")
    print(f"Activos: {symbols}")
    print(f"Primario: {cfg.primary_symbol}")
    print(f"Período: {cfg.start_date} → {cfg.end_date}")
    print("=" * 70)

    # Fetch data
    print("\nDescargando datos...")
    data_4h = {}
    data_daily = {}
    for symbol in symbols:
        print(f"  {symbol} 4h...")
        data_4h[symbol] = fetch_futures_klines(symbol, "4h", cfg.start_date, cfg.end_date)
        print(f"  {symbol} daily...")
        data_daily[symbol] = fetch_futures_klines(symbol, "1d", cfg.start_date, cfg.end_date)

    # Run backtest
    print("\nEjecutando backtest multi-activo...")
    engine = MultiAssetEngine(cfg)
    trades, eq, regime_log = engine.run(data_4h, data_daily)

    # Metrics
    m = calc_metrics(trades, eq, cfg.initial_capital)
    y = yearly_table(eq, cfg.initial_capital)

    print("\n=== Multi-Asset Regime-Switching Results ===")
    print(pd.DataFrame([m]).to_string(index=False))
    print("\nRetornos anuales (%):")
    print(y.to_string(index=False))

    # Symbol breakdown
    if not trades.empty:
        print("\n=== Trades por Símbolo ===")
        sym_stats = trades.groupby("symbol").agg(
            trades=("pnl", "count"),
            win_rate=("pnl", lambda x: (x > 0).mean() * 100),
            total_pnl=("pnl", "sum"),
            avg_pnl=("pnl", "mean"),
        ).round(2)
        print(sym_stats.to_string())

    # Regime analysis
    if not regime_log.empty:
        print("\n=== Régimen Analysis ===")
        primary_trending_pct = regime_log["primary_trending"].mean() * 100
        print(f"  BTC en tendencia: {primary_trending_pct:.1f}% del tiempo")
        print(f"  BTC en rango: {100 - primary_trending_pct:.1f}% del tiempo")

        sym_selection = regime_log["selected_symbol"].value_counts(normalize=True) * 100
        print(f"\n  Tiempo operando cada activo:")
        for sym, pct in sym_selection.items():
            print(f"    {sym}: {pct:.1f}%")

    # Save results
    trades.to_csv(out_dir / "multi_asset_trades.csv", index=False)
    eq.to_csv(out_dir / "multi_asset_equity.csv")
    y.to_csv(out_dir / "multi_asset_yearly.csv", index=False)
    pd.DataFrame([m]).to_csv(out_dir / "multi_asset_summary.csv", index=False)
    regime_log.to_csv(out_dir / "regime_log.csv")

    print(f"\nArchivos en: {out_dir}")


if __name__ == "__main__":
    main()
