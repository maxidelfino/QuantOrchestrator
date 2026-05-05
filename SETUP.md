# QuantOrchestrator Setup Guide

Guía paso a paso para instalar todo desde cero.

La idea es instalar en este orden:

1. Dependencias (Chrome, Node.js, Git)
2. OpenCode
3. gentle-ai
4. QuantOrchestrator
5. TradingView MCP
6. TradingView Desktop / Chrome con debug port

---

## 1) Instalar dependencias

### Windows — setup automático (recomendado)

Doble clic en `scripts\setup.bat` o corré desde PowerShell:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup-tradingview.ps1
```

El script instala automáticamente:
- **Google Chrome** (vía winget)
- **Node.js LTS** (vía winget)
- **Git** (vía winget)

Y después clona el MCP, instala dependencias, y crea el launcher.

### Windows — manual

Si preferís instalar a mano:

#### Chrome

1. Ir a https://www.google.com/chrome/
2. Descargar e instalar

#### Node.js

1. Ir a https://nodejs.org/en/download
2. Descargar el instalador **LTS** (.msi)
3. Ejecutar y dejar opciones por defecto
4. Verificar en terminal nueva:

```powershell
node --version
npm --version
```

#### Git

1. Ir a https://git-scm.com/download/win
2. Descargar e instalar
3. Verificar:

```powershell
git --version
```

### macOS

#### Node.js

Opción simple: instalador oficial LTS desde https://nodejs.org/en/download

O con Homebrew:

```bash
brew install node
```

Verificar:

```bash
node -v
npm -v
```

#### Git

```bash
brew install git
git --version
```

#### Chrome

Descargar de https://www.google.com/chrome/ o:

```bash
brew install --cask google-chrome
```

### Linux

```bash
# Node.js (usar nvm o el gestor de tu distro)
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.4/install.sh | bash
source "$HOME/.nvm/nvm.sh"
nvm install 24

# Git (usualmente preinstalado)
sudo apt install git

# Chrome
wget https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
sudo dpkg -i google-chrome-stable_current_amd64.deb
```

---

## 2) Instalar OpenCode

Web oficial: https://opencode.ai/

### macOS

```bash
brew install anomalyco/tap/opencode
```

### Windows

```powershell
npm install -g opencode-ai
```

### Verificación

```bash
opencode
```

Si abre correctamente, seguís al paso siguiente.

---

## 3) Instalar gentle-ai

Repositorio oficial: https://github.com/Gentleman-Programming/gentle-ai

`gentle-ai` es el stack global que te agrega:

- Engram
- SDD
- Context7
- judgment-day
- skills y tooling de desarrollo

### macOS

```bash
curl -fsSL https://raw.githubusercontent.com/Gentleman-Programming/gentle-ai/main/scripts/install.sh | bash
```

O con Homebrew:

```bash
brew tap Gentleman-Programming/homebrew-tap
brew install gentle-ai
```

### Windows

```powershell
irm https://raw.githubusercontent.com/Gentleman-Programming/gentle-ai/main/scripts/install.ps1 | iex
```

O con Scoop:

```powershell
scoop bucket add gentleman https://github.com/Gentleman-Programming/scoop-bucket
scoop install gentle-ai
```

### Verificación

Seguí la documentación oficial del repo para completar la configuración en OpenCode.

---

## 4) Clonar QuantOrchestrator

```bash
git clone https://github.com/maxidelfino/QuantOrchestrator.git
cd QuantOrchestrator
```

### Qué aporta este repo

`QuantOrchestrator` **no** reinstala SDD ni Engram.

Este repo actúa como un **overlay local** sobre tu stack global de OpenCode + gentle-ai y aporta:

- agente `QuantOrchestrator`
- subagentes de trading
- integración local con TradingView MCP
- scripts de setup para Windows

### Skills requeridas (instalar dentro del repo)

Los subagentes de trading necesitan estas skills. Instalalas desde skills.sh **después de clonar**:

```bash
# Desde la raíz de QuantOrchestrator
npx skills add omer-metin/skills-for-antigravity          # algorithmic-trading + technical-analysis
npx skills add obra/superpowers                             # brainstorming
npx skills add personamanagmentlayer/pcl                    # trading-expert
npx skills add jeremylongshore/claude-code-plugins-plus-skills  # backtesting-trading-strategies
npx skills add 0xhubed/agent-trading-arena                  # risk-management
npx skills add vercel-labs/skills                           # find-skills
```

Verificá que se generó `skills-lock.json` en la raíz del repo.

---

## 5) Instalar TradingView MCP

Este proyecto espera el MCP en esta ruta:

- `./tradingview-mcp/src/server.js`

> **Importante**: `tradingview-mcp/` está en `.gitignore` — no se pushea al repo. Cada persona que clone QuantOrchestrator tiene que clonar el MCP por su cuenta.

### Windows — setup automático (recomendado)

Si ya corriste `scripts\setup.bat` en el paso 1, el MCP ya está instalado. Salteá esta sección.

Si no, doble clic en `scripts\setup.bat` ahora.

### Cualquier plataforma — manual

```bash
# Desde la raíz del repo QuantOrchestrator
git clone https://github.com/LewisWJackson/tradingview-mcp-jackson.git tradingview-mcp
cd tradingview-mcp
npm install
```

En Windows:

```cmd
cd tradingview-mcp
npm install
copy rules.example.json rules.json
cd ..
```

En macOS/Linux:

```bash
cd tradingview-mcp
npm install
cp rules.example.json rules.json
cd ..
```

Si querés el MCP en otra ubicación, vas a tener que editar `opencode.json` y cambiar el path de `./tradingview-mcp/src/server.js`.

---

## 6) Levantar TradingView con debug port

TradingView necesita correr con el puerto de debugging habilitado para que el MCP se conecte.

### Windows (Chrome — recomendado)

> **¿Por qué Chrome y no la app de TradingView?**
> La app de TradingView para Windows viene por Microsoft Store (MSIX/UWP). Las apps MSIX descartan los flags de debugging al arrancar. Sin `--remote-debugging-port=9222`, el MCP no puede conectarse. **Chrome + TradingView web funciona idéntico.**

Doble clic en `scripts\launch-tv.bat`.

O si usaste el setup automático, también hay un acceso directo en tu Escritorio.

El launcher:
1. Detecta Chrome automáticamente (Program Files, Program Files x86, o LocalAppData).
2. Crea un perfil dedicado (`%USERPROFILE%\tv-cdp-profile`) para no interferir con tu Chrome personal.
3. Lanza Chrome con TradingView y el puerto 9222.

**Primera vez**: vas a tener que loguearte en TradingView en la ventana de Chrome que se abre. Las siguientes veces ya va a recordar tu sesión.

**Verificación rápida**: abrí `http://localhost:9222/json/version` en cualquier navegador. Si devuelve JSON, CDP está funcionando.

### macOS (TradingView Desktop)

```bash
/Applications/TradingView.app/Contents/MacOS/TradingView --remote-debugging-port=9222
```

O usá el launcher incluido en el MCP:

```bash
./tradingview-mcp/scripts/launch_tv_debug_mac.sh
```

### Linux

```bash
./tradingview-mcp/scripts/launch_tv_debug_linux.sh
```

### Verificación

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

## 7) Abrir OpenCode dentro de QuantOrchestrator

Muy importante: abrí OpenCode **dentro de la carpeta del repo**.

```bash
cd QuantOrchestrator
opencode
```

Ahí deberías tener disponible el agente:

- `QuantOrchestrator`

Si abrís OpenCode en otra carpeta, no vas a ver este agente, y eso es correcto.

---

## 8) Verificar conexión con TradingView

En OpenCode, escribí:

```
Usá tv_health_check
```

Deberías ver:

```json
{
  "success": true,
  "cdp_connected": true
}
```

Si ves `cdp_connected: false`, revisá:

1. ¿Chrome con TradingView está abierto con el launcher?
2. ¿El puerto 9222 está libre? (cerrá otros Chrome eerst)
3. ¿Estás dentro de la carpeta `QuantOrchestrator` al abrir OpenCode?

---

## Resumen conceptual

### Global (una vez por máquina)

- Chrome
- Node.js
- Git
- OpenCode
- gentle-ai

### Local al repo (cada clone)

- QuantOrchestrator
- TradingView MCP (clonado dentro del repo)

---

## Uso diario

1. Doble clic en `launch-tv.bat` → Chrome abre con TradingView (ya logueado).
2. Abrir OpenCode dentro de la carpeta `QuantOrchestrator`.
3. Listo.

**FAQ rápido**:

- *¿Tengo que dejar Chrome abierto?* → Sí, mientras uses el MCP.
- *¿Tengo que re-loguearme cada vez?* → No, el perfil se guarda en `%USERPROFILE%\tv-cdp-profile`.
- *¿Puedo usar mi Chrome normal en paralelo?* → Sí, son perfiles separados.

---

## Troubleshooting rápido

### `cdp_connected: false`

- Verificá que Chrome con TradingView esté abierto (usá el launcher).
- Verificá `http://localhost:9222/json/version` en tu navegador.
- Cerrá todos los Chrome y relanzá el `.bat`.

### `ECONNREFUSED`

- TradingView Desktop o Chrome no están corriendo con CDP.
- El puerto 9222 puede estar en uso por otro proceso.

### `node` o `npm` no existen

- Node.js no quedó bien instalado o la terminal no se reinició.
- Cerrá la terminal y abrí una nueva.

### `opencode` no existe

- OpenCode no está instalado globalmente o no está en PATH.
- Probá `npm install -g opencode-ai`.

### No aparece el agente `QuantOrchestrator`

- Estás abriendo OpenCode fuera de la carpeta del repo.
- `cd QuantOrchestrator` y reabrir OpenCode ahí.

### TradingView MCP no conecta

Revisá estas 3 cosas:

1. `tradingview-mcp` existe en `./tradingview-mcp` dentro del repo.
2. Las dependencias están instaladas (`npm install` dentro de `tradingview-mcp`).
3. TradingView / Chrome está corriendo con `--remote-debugging-port=9222`.

### Port 9222 already in use

- Cerrá todos los Chrome y relanzá el `.bat`.
- O cambiá el puerto en el launcher y en la config del MCP.

### Claude dice "no tengo permisos para winget"

- Corré el script o el terminal como administrador.

---

## Créditos

- **TradingView MCP**: [LewisWJackson/tradingview-mcp-jackson](https://github.com/LewisWJackson/tradingview-mcp-jackson) — fork con mejoras del original de [@tradesdontlie](https://github.com/tradesdontlie/tradingview-mcp).
- **Setup Windows**: Adaptado de [kmanus88/tradingview-claude-code-windows](https://github.com/kmanus88/tradingview-claude-code-windows).

---

## Recomendación final

Si estás siguiendo un video o tutorial de instalación, usá este archivo como checklist.

La secuencia correcta es:

1. Dependencias (Chrome, Node.js, Git)
2. OpenCode
3. gentle-ai
4. QuantOrchestrator (clonar)
5. TradingView MCP (clonar dentro del repo)
6. TradingView Desktop / Chrome con debug port
7. Verificar conexión con `tv_health_check`
8. Recién ahí usar `QuantOrchestrator`