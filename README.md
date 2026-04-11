# QuantOrchestrator

> Si querés una guía paso a paso para instalar todo desde cero, mirá [`SETUP.md`](./SETUP.md).

## Qué es

**QuantOrchestrator** es un agente principal orientado a **trading algorítmico** y **creación de bots de trading**.

Su trabajo no es ejecutar todo inline, sino **orquestar** el flujo correcto: aclarar el problema, delegar al subagente adecuado, sintetizar resultados y empujar decisiones con criterio.

Está pensado para trabajar sobre ideas y sistemas relacionados con:

- estrategias cuantitativas
- bots de ejecución
- arbitrage
- MEV / sniping
- perp futures
- acciones / ETFs / futuros / crypto
- backtesting
- risk engineering
- prediction markets

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
- `strategy-architect` → diseño de estrategias e hipótesis
- `backtesting-engineer` → validación histórica y backtests
- `execution-engineer` → execution engines, venues y adapters
- `risk-engineer` → sizing, límites y protección de capital
- `market-structure-researcher` → DEX, MEV, sniping, arbitrage, microstructure
- `prediction-market-quant` → estrategias para prediction markets
- Integración local con TradingView MCP

Si abrís OpenCode fuera de este repo, **no** vas a tener el agente `QuantOrchestrator`.
Si lo abrís dentro de este repo, **sí**.

---

## Dependencias globales esperadas

Este repo **no redefine** localmente:

- SDD
- Engram
- Context7
- judgment-day

Se asume que eso ya viene de tu instalación global de **gentle-ai**.

---

## Instalación recomendada

### Paso 1: instalar OpenCode globalmente

Instalá OpenCode normalmente en tu sistema operativo.

### Paso 2: instalar gentle-ai globalmente

Instalá y configurá `gentle-ai` en tu entorno global de OpenCode.

La idea es que `gentle-ai` resuelva una vez por máquina:

- Engram
- SDD
- Context7
- judgment-day
- perfiles generales de desarrollo

### Paso 3: clonar este repo

```bash
git clone <repo-url> QuantOrchestrator
cd QuantOrchestrator
```

### Paso 4: instalar TradingView MCP localmente

La configuración local apunta a:

- `./tradingview-mcp/src/server.js`

La opción más simple es clonar `tradingview-mcp` dentro de este repo:

```bash
git clone https://github.com/tradesdontlie/tradingview-mcp.git tradingview-mcp
cd tradingview-mcp
npm install
cd ..
```

Si querés tener `tradingview-mcp` en otra ruta, podés hacerlo, pero vas a tener que editar `opencode.json`.

### Paso 5: abrir OpenCode dentro de este repo

Abrí OpenCode parado en la carpeta `QuantOrchestrator`.

Ahí vas a tener disponible el agente:

- `QuantOrchestrator`

---

## Integración con TradingView MCP

QuantOrchestrator puede usar **TradingView MCP** como apoyo para:

- research de charts
- validación visual/técnica de setups
- lectura de contexto de símbolo/timeframe
- lectura de indicadores y estado de chart
- screenshots y soporte de análisis

### Importante

TradingView MCP **NO**:

- prueba edge por sí mismo
- reemplaza backtesting
- reemplaza validación cuantitativa
- es infraestructura de ejecución
- es conexión a broker o venue

Es una herramienta de **research + validación contextual**, no una prueba definitiva de que una estrategia sirve en vivo.

### Subagentes que tienen acceso a TradingView MCP

Solo está habilitado para:

- `strategy-architect`
- `backtesting-engineer`
- `market-structure-researcher`
- `risk-engineer`

---

## Cómo levantar TradingView Desktop con debug port

TradingView Desktop debe correr localmente con CDP habilitado.

### macOS

```bash
/Applications/TradingView.app/Contents/MacOS/TradingView --remote-debugging-port=9222
```

### Windows

```powershell
& "$env:LOCALAPPDATA\Programs\TradingView\TradingView.exe" --remote-debugging-port=9222
```

Si lo tenés instalado en otra ubicación, ajustá la ruta manualmente.

### Check rápido

Desde el repo `tradingview-mcp`:

```bash
node src/cli/index.js status
```

Si todo está bien, deberías ver algo equivalente a:

```json
{
  "success": true,
  "cdp_connected": true,
  "api_available": true
}
```

---

## Nota importante sobre paths

Hoy `QuantOrchestrator/opencode.json` apunta a:

- `./tradingview-mcp/src/server.js`

Si clonás `tradingview-mcp` en otra ubicación, **vas a tener que editar ese path** en `opencode.json`.

Dicho más simple: si el repo no está donde la config lo espera, el MCP no va a levantar.

---

## Cómo se integra con SDD

`QuantOrchestrator` usa el stack global de `gentle-ai`.

Eso significa que:

- el repo **no** redefine agentes `sdd-*`
- el repo **no** levanta un MCP local de Engram
- el repo **no** duplica Context7 ni judgment-day

Si tu instalación global de `gentle-ai` está sana, podés usar SDD desde este repo sin copiar prompts ni tocar rutas absolutas.

---

## Guardrails

- No diseñar bots solo por intuición.
- No tomar chart context como prueba de edge.
- No confundir research tooling con execution infra.
- No saltear riesgo, costos, slippage, latency y failure modes.
- No asumir que una integración local está lista sin verificar paths y dependencias.

---

## Próximo paso recomendado

1. Instalar OpenCode globalmente
2. Instalar y configurar gentle-ai globalmente
3. Clonar `QuantOrchestrator`
4. Clonar `tradingview-mcp` dentro del repo
5. Levantar TradingView Desktop con debug port
6. Hacer un health check del MCP
7. Recién ahí usar `QuantOrchestrator`
