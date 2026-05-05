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
- `PromptEngineer` → producción de prompts listos para copiar y pegar en OpenCode y flujos LLM
- `strategy-architect` → diseño de estrategias e hipótesis
- `backtesting-engineer` → validación histórica y backtests
- `execution-engineer` → execution engines, venues y adapters
- `risk-engineer` → sizing, límites y protección de capital
- `market-structure-researcher` → DEX, MEV, sniping, arbitrage, microstructure
- `prediction-market-quant` → estrategias para prediction markets
- Integración local con TradingView MCP
- Scripts de setup para Windows (`scripts/`)

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
git clone https://github.com/maxidelfino/QuantOrchestrator.git
cd QuantOrchestrator
```

### Paso 4: instalar TradingView MCP

La configuración local apunta a:

- `./tradingview-mcp/src/server.js`

#### Windows (recomendado: setup automático)

Doble clic en `scripts\setup.bat` o corré:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup-tradingview.ps1
```

Eso instala Chrome, Node.js y Git si faltan, clona el MCP, instala dependencias, y crea el launcher.

#### Manual (cualquier plataforma)

```bash
git clone https://github.com/LewisWJackson/tradingview-mcp-jackson.git tradingview-mcp
cd tradingview-mcp
npm install
cp rules.example.json rules.json   # o copy en Windows
cd ..
```

Si querés tener `tradingview-mcp` en otra ruta, podés hacerlo, pero vas a tener que editar `opencode.json`.

### Paso 5: levantar TradingView Desktop con debug port

TradingView Desktop o Chrome debe correr con CDP habilitado.

#### Windows (Chrome — recomendado)

Doble clic en `scripts\launch-tv.bat` o en el acceso directo que creó el setup en tu Escritorio.

Eso abre Chrome con TradingView web y el puerto 9222. Logueate (solo la primera vez) y abrí cualquier chart.

> **¿Por qué Chrome y no la app de TradingView?**
> La app de Windows viene por Microsoft Store (MSIX) y no acepta los flags de debugging que el MCP necesita. Chrome + TradingView web funciona igual y sí los acepta.

#### macOS / Linux (TradingView Desktop)

```bash
# macOS
/Applications/TradingView.app/Contents/MacOS/TradingView --remote-debugging-port=9222

# Linux
./scripts/launch_tv_debug_linux.sh
```

### Paso 6: abrir OpenCode dentro de este repo

```bash
cd QuantOrchestrator
opencode
```

Ahí vas a tener disponible el agente:

- `QuantOrchestrator`

### Paso 7: verificar conexión

En OpenCode, escribí:

```
Usá tv_health_check
```

Deberías ver `cdp_connected: true`.

---

## Integración con TradingView MCP

QuantOrchestrator puede usar **TradingView MCP** como apoyo para:

- research de charts
- validación visual/técnica de setups
- lectura de contexto de símbolo/timeframe
- lectura de indicadores y estado de chart
- screenshots y soporte de análisis
- morning brief con reglas personalizables (`rules.json`)

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

## Fundamentos del setup en Windows

El setup de TradingView MCP en Windows tiene un detalle importante:

- **La app de TradingView para Windows** viene por Microsoft Store (MSIX/UWP).
- Las apps MSIX **descartan** los flags de debugging al arrancar.
- Sin `--remote-debugging-port=9222`, el MCP no puede conectarse.
- **Solución**: Chrome normal + TradingView web con CDP habilitado. Funciona idéntico.

El launcher `scripts\launch-tv.bat` se encarga de:

1. Detectar Chrome automáticamente (Program Files, Program Files x86, o LocalAppData).
2. Crear un perfil dedicado (`tv-cdp-profile`) para no interferir con tu Chrome personal.
3. Lanzar Chrome con TradingView y el puerto 9222.

---

## Nota importante sobre paths

`opencode.json` apunta a:

- `./tradingview-mcp/src/server.js`

Si clonás `tradingview-mcp` en otra ubicación, **vas a tener que editar ese path** en `opencode.json`.

El directorio `tradingview-mcp/` está en `.gitignore` — no se pushea al repo. Cada persona que clone QuantOrchestrator tiene que clonar el MCP por su cuenta.

---

## Cómo se integra con SDD

`QuantOrchestrator` usa el stack global de `gentle-ai`.

Eso significa que:

- el repo **no** redefine agentes `sdd-*`
- el repo **no** levanta un MCP local de Engram
- el repo **no** duplica Context7 ni judgment-day

Si tu instalación global de `gentle-ai` está sana, podés usar SDD desde este repo sin copiar prompts ni tocar rutas absolutas.

---

## Scripts incluidos

| Script | Plataforma | Qué hace |
|--------|-----------|----------|
| `scripts\launch-tv.bat` | Windows | Lanza Chrome con CDP para TradingView web |
| `scripts\setup.bat` | Windows | Wrapper para ejecutar `setup-tradingview.ps1` con un doble clic |
| `scripts\setup-tradingview.ps1` | Windows | Setup automático: instala deps, clona MCP, configura launcher |
| `tradingview-mcp\scripts\launch_tv_debug_linux.sh` | Linux | Launcher nativo para TradingView Desktop en Linux |
| `tradingview-mcp\scripts\launch_tv_debug_mac.sh` | macOS | Launcher nativo para TradingView Desktop en macOS |

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

---

## Próximo paso recomendado

1. Instalar OpenCode globalmente
2. Instalar y configurar gentle-ai globalmente
3. Clonar `QuantOrchestrator`
4. Correr `scripts\setup.bat` (Windows) o clonar el MCP manualmente
5. Levantar TradingView Desktop / Chrome con debug port
6. Hacer un health check del MCP
7. Recién ahí usar `QuantOrchestrator`