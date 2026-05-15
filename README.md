# QuantOrchestrator

> Si querés una guía paso a paso para instalar todo desde cero, mirá [`SETUP.md`](./SETUP.md).

## Qué es

**QuantOrchestrator** es un agente principal orientado a **trading algorítmico** y **creación de bots de trading**.

Su trabajo no es ejecutar todo inline, sino **orquestar** el flujo correcto: aclarar el problema, delegar al subagente adecuado, sintetizar resultados y empujar decisiones con criterio.

---

## Trading Bots

Este repo incluye dos estrategias Python para BTC perpetual futures y un bot MM en TypeScript:

| Bot | Timeframe | Strategy | Status |
|-----|-----------|----------|--------|
| [`bots/python/btc_trend_4h/`](bots/python/btc_trend_4h/) | 4h + daily | EMA trend-following with regime filter | Active |
| [`bots/python/btc_momentum_1h/`](bots/python/btc_momentum_1h/) | 1h | RSI momentum pullback with ADX filter | Active |
| [`bots/typescript/mm_bot_01/`](bots/typescript/mm_bot_01/) | Multi-timeframe | Market making + signal overlays | Active |

### Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure
cp .env.example .env
# Edit .env with your API keys

# 3. Run Python bots
python -m bots.python.btc_trend_4h
python -m bots.python.btc_momentum_1h

# 4. TypeScript compile check
cd bots/typescript/mm_bot_01 && npx tsc --noEmit
```

### Architecture

```
exchanges/
├── python/                    # Hyperliquid + Binance Python adapters
└── typescript/                # Zerone SDK + Binance feed adapters

bots/
├── python/
│   ├── core/                  # Shared Python bot framework
│   ├── btc_trend_4h/
│   ├── btc_momentum_1h/
│   └── template/
└── typescript/
    ├── core/                  # Shared TS types/logger
    ├── mm_bot_01/
    └── template/

backtest-results/
└── hyperliquid/               # Consolidated active backtest artifacts
```

### Configuration

- **`.env`** — Secrets only (API keys, wallet addresses, private keys)
- **`bots/python/*/config.yaml`** — Strategy-specific parameters with full documentation
- Environment variables override YAML defaults

See `.env.example` for all available environment variables.

---

## Filosofía

QuantOrchestrator sigue estas reglas:

- **EDGE antes que código**
- **Riesgo antes que retorno**
- **Execution matters**
- **Backtest no es verdad**
- **Microstructure importa**
- **Delegación por defecto**

La idea es simple: si no hay hipótesis operable, riesgo entendible y validación razonable, no hay bot serio.

---

## Arquitectura del proyecto

Este repo está pensado como un **overlay local de dominio** sobre tu stack global de OpenCode + gentle-ai.

### Global

Se instala una vez por máquina:

- OpenCode
- gentle-ai

Eso te da el stack reutilizable para cualquier proyecto:

- Engram
- SDD
- Context7
- judgment-day
- tooling general de desarrollo

### Local a este repo

`QuantOrchestrator` aporta sólo lo específico del dominio trading:

- agente `QuantOrchestrator` → orquestador puro
- `PromptEngineer` → producción de prompts listos para copiar y pegar en OpenCode y flujos LLM
- `strategy-architect` → diseño de estrategias e hipótesis
- `backtesting-engineer` → validación histórica y backtests
- `execution-engineer` → execution engines, venues y adapters
- `risk-engineer` → sizing, límites y protección de capital
- `market-structure-researcher` → DEX, MEV, sniping, arbitrage, microstructure
- `prediction-market-quant` → estrategias para prediction markets
- Integración local con TradingView MCP
- Scripts de setup para Windows (`scripts/`)
- Trading bots (`bots/`)

Si abrís OpenCode fuera de este repo, **no** vas a tener el agente `QuantOrchestrator`.
Si lo abrís dentro de este repo, **sí**.

---

## Instalación recomendada

### Paso 1: instalar OpenCode globalmente

Instalá OpenCode normalmente en tu sistema operativo.

### Paso 2: instalar gentle-ai globalmente

Instalá y configurá `gentle-ai` en tu entorno global de OpenCode.

### Paso 3: clonar este repo

```bash
git clone https://github.com/maxidelfino/QuantOrchestrator.git
cd QuantOrchestrator
```

### Paso 4: instalar dependencias Python

```bash
pip install -r requirements.txt
```

### Paso 5: configurar el bot

```bash
cp .env.example .env
# Edit .env with your API keys
```

### Paso 6: instalar TradingView MCP

La configuración local apunta a:

- `./tradingview-mcp/src/server.js`

#### Windows (recomendado: setup automático)

Doble clic en `scripts\setup.bat` o corré:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup-tradingview.ps1
```

#### Manual (cualquier plataforma)

```bash
git clone https://github.com/LewisWJackson/tradingview-mcp-jackson.git tradingview-mcp
cd tradingview-mcp
npm install
cp rules.example.json rules.json   # o copy en Windows
cd ..
```

### Paso 7: levantar TradingView Desktop con debug port

#### Windows (Chrome — recomendado)

Doble clic en `scripts\launch-tv.bat`.

#### macOS / Linux (TradingView Desktop)

```bash
# macOS
/Applications/TradingView.app/Contents/MacOS/TradingView --remote-debugging-port=9222
```

### Paso 8: abrir OpenCode dentro de este repo

```bash
cd QuantOrchestrator
opencode
```

---

## Guardrails

- No diseñar bots solo por intuición.
- No tomar chart context como prueba de edge.
- No confundir research tooling con execution infra.
- No saltear riesgo, costos, slippage, latency y failure modes.
- No asumir que una integración local está lista sin verificar paths y dependencias.

---

## Créditos

- **TradingView MCP**: [LewisWJackson/tradingview-mcp-jackson](https://github.com/LewisWJackson/tradingview-mcp-jackson) — fork con mejoras del original de [@tradesdontlie](https://github.com/tradesdontlie/tradingview-mcp).
- **Setup Windows**: Adaptado de [kmanus88/tradingview-claude-code-windows](https://github.com/kmanus88/tradingview-claude-code-windows).
