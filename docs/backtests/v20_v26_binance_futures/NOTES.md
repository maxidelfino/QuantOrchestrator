# Notas de backtest (v20 vs v26)

## Supuestos implementados

- Dataset: Binance **USDⓈ-M Futures** (`fapi/v1/klines`) BTCUSDT 15m.
- Regime 1h: derivado por resample desde 15m y mapeado con `merge_asof(direction="backward")` (sin lookahead).
- Costos: comisión + slippage por lado, configurables por CLI.
- Fills: causales con órdenes stop pendientes, cancelación a `>2` barras, sin leakage de señales futuras.
- Kill-switches replicados: soft/daily/hard kill como en la lógica Pine de la familia.
- v26: parcial 50% en 1R + runner con BE/trailing 1.5 ATR tras parcial.

## Hook de funding

- El script soporta `--with-funding`.
- Si se activa, descarga `fapi/v1/fundingRate` y aplica flujo de caja en timestamps de funding.
- Convención usada: en perp linear, **long paga** si funding > 0, short cobra.

## Limitaciones importantes

- Modelado intrabar OHLC: cuando hay ambigüedad TP/SL en la misma vela, no hay secuencia de ticks real.
- `calc_on_order_fills` de Pine tiene detalles de simulación internos no 100% replicables fuera de TradingView.
- Esta familia tiene hard-kill agresivo (-4.5% desde pico). Si dispara temprano, puede “apagar” casi todo el histórico.
