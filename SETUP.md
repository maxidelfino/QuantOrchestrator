# QuantOrchestrator Setup Guide

Esta guía está pensada para alguien que quizás **no viene del mundo del desarrollo de software**.

La idea es instalar todo en este orden:

1. Node.js
2. OpenCode
3. gentle-ai
4. QuantOrchestrator
5. TradingView MCP
6. TradingView Desktop con debug port

---

## 1) Instalar Node.js

Node.js es necesario para instalar y correr varias partes del stack.

Sitio oficial:

- [https://nodejs.org/en/download](https://nodejs.org/en/download)

### Windows

Para alguien que **no sabe nada de desarrollo**, lo más simple es:

1. entrar a la web oficial
2. descargar el archivo **Node.js LTS Installer (.msi)** para Windows
3. ejecutar el instalador y dejar las opciones por defecto  


> No recomiendo arrancar con Chocolatey para un usuario totalmente nuevo. Funciona, sí, pero primero obliga a instalar otro gestor y agrega complejidad innecesaria.

#### Opción simple recomendada

Descargá el instalador oficial **LTS** desde la web y ejecutá el archivo **`.msi`**.

#### Opción por comando (avanzada)

Si ya sabés lo que estás haciendo y querés usar Chocolatey:

```powershell
# Download and install Chocolatey:
powershell -c "irm https://community.chocolatey.org/install.ps1|iex"

# Download and install Node.js:
choco install nodejs --version="24.14.1"
```

Después de instalar, abrí una terminal nueva y verificá:

```powershell
# Verify the Node.js version:
node -v

# Verify npm version:
npm -v
```

### macOS

Para alguien no técnico, lo más simple es usar:

- el instalador oficial de Node.js desde la web, o
- Homebrew si ya lo tiene instalado

#### Opción simple recomendada

Descargá el instalador oficial **LTS** desde:

- [https://nodejs.org/en/download](https://nodejs.org/en/download)

En macOS, el archivo que tenés que ejecutar es el **Node.js LTS Installer (.pkg)**.

#### Opción recomendada para gente técnica

Usar **Homebrew** para manejar dependencias más fácil.

Si ya tenés Homebrew:

```bash
brew install node
```

#### Opción avanzada con nvm

La web oficial también muestra instalación con `nvm`:

```bash
# Download and install nvm:
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.4/install.sh | bash

# in lieu of restarting the shell
\. "$HOME/.nvm/nvm.sh"

# Download and install Node.js:
nvm install 24
```

Esto funciona, pero para una persona totalmente nueva suele ser más simple el instalador oficial o Homebrew.

Verificación:

```bash
# Verify the Node.js version:
node -v

# Verify npm version:
npm -v
```

---

## 2) Instalar OpenCode

Web oficial:

- [https://opencode.ai/](https://opencode.ai/)
- Docs: [https://opencode.ai/docs](https://opencode.ai/docs)

### macOS

Recomendado: usar **Homebrew**.

```bash
brew install anomalyco/tap/opencode
```

### Windows

OpenCode recomienda idealmente **WSL** para la mejor experiencia, pero también puede instalarse en Windows nativo.

Si ya instalaste Node.js, una opción simple es:

```powershell
npm install -g opencode-ai
```

Alternativas oficiales:

- Scoop
- Chocolatey
- Releases

Consultá siempre la doc oficial por si cambia el método recomendado:

- [https://opencode.ai/docs](https://opencode.ai/docs)

### Verificación

```bash
opencode
```

Si abre correctamente, seguís al paso siguiente.

---

## 3) Instalar gentle-ai

Repositorio oficial:

- [https://github.com/Gentleman-Programming/gentle-ai](https://github.com/Gentleman-Programming/gentle-ai)

`gentle-ai` es el stack global que te agrega:

- Engram
- SDD
- Context7
- judgment-day
- skills y tooling de desarrollo

### macOS

Recomendado: usar el instalador oficial del proyecto.

```bash
curl -fsSL https://raw.githubusercontent.com/Gentleman-Programming/gentle-ai/main/scripts/install.sh | bash
```

Alternativa para gente más técnica: **Homebrew**.

```bash
brew tap Gentleman-Programming/homebrew-tap
brew install gentle-ai
```

### Windows

Recomendado: **Scoop**.

```powershell
scoop bucket add gentleman https://github.com/Gentleman-Programming/scoop-bucket
scoop install gentle-ai
```

Alternativa oficial en PowerShell:

```powershell
irm https://raw.githubusercontent.com/Gentleman-Programming/gentle-ai/main/scripts/install.ps1 | iex
```

### Verificación

Seguí la documentación oficial del repo para completar la configuración inicial en OpenCode.

La referencia principal es:

- [https://github.com/Gentleman-Programming/gentle-ai](https://github.com/Gentleman-Programming/gentle-ai)

---

## 4) Clonar QuantOrchestrator

```bash
git clone <repo-url> QuantOrchestrator
cd QuantOrchestrator
```

### Qué aporta este repo

`QuantOrchestrator` **no** reinstala SDD ni Engram.

Este repo actúa como un **overlay local** sobre tu stack global de OpenCode + gentle-ai y aporta:

- agente `QuantOrchestrator`
- subagentes de trading
- integración local con TradingView MCP

---

## 5) Instalar TradingView MCP dentro del repo

Este proyecto espera el MCP en esta ruta:

- `./tradingview-mcp/src/server.js`

La forma más simple es clonar el repo adentro de `QuantOrchestrator`:

```bash
git clone https://github.com/tradesdontlie/tradingview-mcp.git tradingview-mcp
cd tradingview-mcp
npm install
cd ..
```

Si lo querés en otra ubicación, vas a tener que editar `opencode.json`.

---

## 6) Levantar TradingView Desktop con debug port

TradingView Desktop tiene que correr en otra terminal con el puerto de debug habilitado.

### macOS

```bash
/Applications/TradingView.app/Contents/MacOS/TradingView --remote-debugging-port=9222
```

### Windows

```powershell
& "$env:LOCALAPPDATA\Programs\TradingView\TradingView.exe" --remote-debugging-port=9222
```

Si TradingView está instalado en otra ruta, ajustala manualmente.

---

## 7) Verificar TradingView MCP

Parado dentro de `tradingview-mcp`:

```bash
node src/cli/index.js status
```

Si todo está bien, deberías ver una respuesta parecida a:

```json
{
  "success": true,
  "cdp_connected": true,
  "api_available": true
}
```

---

## 8) Abrir OpenCode dentro de QuantOrchestrator

Muy importante: abrí OpenCode **dentro de la carpeta del repo**.

```bash
cd QuantOrchestrator
opencode
```

Ahí deberías tener disponible el agente:

- `QuantOrchestrator`

Si abrís OpenCode en otra carpeta, no vas a ver este agente, y eso es correcto.

---

## Resumen conceptual

### Global

- OpenCode
- gentle-ai

### Local al repo

- QuantOrchestrator
- TradingView MCP

---

## Troubleshooting rápido

### `node` o `npm` no existen

Node.js no quedó bien instalado o la terminal no se reinició.

### `opencode` no existe

OpenCode no quedó instalado globalmente o no está en PATH.

### No aparece el agente `QuantOrchestrator`

Estás abriendo OpenCode fuera de la carpeta del repo.

### TradingView MCP no conecta

Revisá estas 3 cosas:

1. que `tradingview-mcp` exista en `./tradingview-mcp`
2. que sus dependencias estén instaladas
3. que TradingView Desktop esté abierto con `--remote-debugging-port=9222`

---

## Recomendación final

Si estás siguiendo un video o tutorial de instalación, usá este archivo como checklist.

La secuencia correcta es:

1. Node.js
2. OpenCode
3. gentle-ai
4. QuantOrchestrator
5. TradingView MCP
6. TradingView Desktop con debug port
