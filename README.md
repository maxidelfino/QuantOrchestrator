# QuantOrchestrator

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

## Cómo está organizado

### Agente principal

- `QuantOrchestrator` → orquestador puro

### Subagentes visibles

- `strategy-architect` → diseño de estrategias e hipótesis
- `backtesting-engineer` → validación histórica y backtests
- `execution-engineer` → execution engines, venues y adapters
- `risk-engineer` → sizing, límites y protección de capital
- `market-structure-researcher` → DEX, MEV, sniping, arbitrage, microstructure
- `prediction-market-quant` → estrategias para prediction markets
- `judgment-day` → review adversarial

### Subagentes ocultos

Incluye jueces y agentes SDD para flujos más avanzados de implementación, validación y archivo.

---

## Comandos principales

- `/strategy <idea>`
- `/backtest <strategy>`
- `/bot-new <name>`
- `/execution <venue>`
- `/arb <pair/venue>`
- `/mev <idea>`
- `/prediction <market>`
- `/risk <strategy>`
- `/judge <scope>`

También soporta **triggers en lenguaje natural**. Ejemplo:

- "quiero crear un bot que haga market making en SOL" → puede rutear internamente como `/bot-new`
- "probemos esta estrategia en datos históricos" → puede rutear como `/backtest`

---

## Modelo de ejecución

En la configuración actual:

- el agente principal `QuantOrchestrator` define el modelo base
- los subagentes **no llevan `model` explícito**
- por lo tanto, **heredan el modelo default del agente principal**

Hoy eso se usa para que todo el ecosistema de QuantOrchestrator quede alineado al mismo modelo principal.

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

No está habilitado, por ahora, para `execution-engineer`, `prediction-market-quant`, `judgment-day`, jueces ni agentes SDD.

---

## Cómo instalar TradingView MCP

Para que esta integración funcione, tenés que clonar localmente el repo de TradingView MCP:

Repo:

- `https://github.com/tradesdontlie/tradingview-mcp`

### Paso 1: clonar el repo

Clonalo localmente en una ubicación que respete el path esperado por `QuantOrchestrator`.

La configuración actual apunta a:

- `./tradingview-mcp/src/server.js`

Eso significa que, si dejás la config como está, el repo debería existir relativo al directorio donde corre esta configuración.

### Paso 2: instalar dependencias

Dentro del repo clonado de `tradingview-mcp`, instalá sus dependencias con Node.js.

### Paso 3: levantar TradingView Desktop con debug port

TradingView Desktop debe correr localmente con CDP habilitado. No alcanza con ejecutar solo `--remote-debugging-port=9222`: ese flag tiene que pasarse al ejecutable de TradingView Desktop.

#### macOS

Ejemplo típico:

```bash
/Applications/TradingView.app/Contents/MacOS/TradingView --remote-debugging-port=9222
```

Si tenés TradingView instalado en otra ruta, usá la ruta real del ejecutable.

#### Windows

Ejemplo típico en PowerShell:

```powershell
& "$env:LOCALAPPDATA\Programs\TradingView\TradingView.exe" --remote-debugging-port=9222
```

O, si lo tenés instalado en otra ubicación, ajustá la ruta manualmente. En algunos entornos también puede estar bajo rutas como:

- `%LOCALAPPDATA%\TradingView\TradingView.exe`
- `%PROGRAMFILES%\WindowsApps\TradingView*\TradingView.exe`

La clave conceptual es siempre la misma: **abrir TradingView Desktop pasándole el flag `--remote-debugging-port=9222`**.

### Paso 4: validar la conexión

Una vez instalado y con TradingView Desktop corriendo en modo debug, podés validar la conexión con el health check que exponga el MCP.

Si querés hacer una validación rápida desde el repo clonado de `tradingview-mcp`, un ejemplo útil es:

```bash
node src/cli/index.js status
```

Si todo está bien, deberías ver una respuesta JSON con señales como estas:

```json
{
  "success": true,
  "cdp_connected": true,
  "api_available": true
}
```

Eso significa que:

- el server puede hablar con TradingView Desktop
- el puerto de debug está accesible
- la API interna necesaria para operar el chart está disponible

### Primer check después del setup

Si levantaste TradingView Desktop correctamente, deberías ver una línea parecida a esta en la terminal:

```text
DevTools listening on ws://127.0.0.1:9222/devtools/browser/...
```

Eso indica que el puerto de debug quedó expuesto correctamente.

Después de eso, el siguiente check lógico es validar desde el MCP con un health check o comando equivalente del server, por ejemplo `tv_health_check` si tu cliente MCP lo expone con ese nombre.

Si **no** aparece la línea `DevTools listening on ws://127.0.0.1:9222/...`, entonces TradingView Desktop no quedó levantado con CDP/debug activo y el MCP no va a poder conectarse.

---

## Nota importante sobre paths

Hoy `QuantOrchestrator/opencode.json` apunta a:

- `./tradingview-mcp/src/server.js`

Si clonás `tradingview-mcp` en otra ubicación, **vas a tener que editar ese path** en `opencode.json`.

Dicho más simple: si el repo no está donde la config lo espera, el MCP no va a levantar.

---

## Integración futura con SDD

Si más adelante querés usar QuantOrchestrator con un flujo SDD más completo, además del setup actual vas a necesitar instalar el repo de:

- `https://github.com/Gentleman-Programming/gentle-ai`

### Qué implica eso

Si instalás `gentle-ai`, probablemente tengas que:

- ajustar los paths de prompts o referencias SDD
- modificar rutas dentro de archivos JSON de configuración
- revisar que los agentes SDD apunten a los archivos correctos en tu máquina

En otras palabras: **no alcanza con clonar el repo**. También hay que revisar la configuración local para que los paths del ecosistema SDD coincidan con tu instalación real.

---

## Guardrails

- No diseñar bots solo por intuición.
- No tomar chart context como prueba de edge.
- No confundir research tooling con execution infra.
- No saltear riesgo, costos, slippage, latency y failure modes.
- No asumir que una integración local está lista sin verificar paths y dependencias.

---

## Estado actual

Al momento de esta documentación:

- QuantOrchestrator ya está configurado para que los subagentes hereden el modelo del agente principal.
- TradingView MCP ya está referenciado en `opencode.json`.
- El repo `tradingview-mcp` debe estar clonado localmente para que esa integración funcione.
- Si en el futuro querés endurecer el flujo con SDD, también vas a necesitar instalar `gentle-ai` y revisar paths.

---

## Próximo paso recomendado

1. Clonar `tradingview-mcp`
2. Verificar/ajustar el path en `opencode.json`
3. Levantar TradingView Desktop con debug port
4. Hacer un health check del MCP
5. Recién ahí correr una prueba real de QuantOrchestrator con contexto de chart
