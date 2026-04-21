# setup-tradingview.ps1
# Setup automatico: TradingView MCP para QuantOrchestrator en Windows.
# Instala Chrome, Node.js, Git. Clona el MCP, instala dependencias.
#
# Uso:
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup-tradingview.ps1
#
# O doble clic en scripts\setup.bat (wrapper que corre este script)

$ErrorActionPreference = "Stop"

function Write-Step($msg) {
    Write-Host ""
    Write-Host ">>> $msg" -ForegroundColor Cyan
}

function Write-Ok($msg) {
    Write-Host "  [OK] $msg" -ForegroundColor Green
}

function Write-Warn($msg) {
    Write-Host "  [!]  $msg" -ForegroundColor Yellow
}

function Write-Err($msg) {
    Write-Host "  [X]  $msg" -ForegroundColor Red
}

# Fixes para codificacion en Windows
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# ================================================================
# 1. Verificar winget
# ================================================================
Write-Step "Verificando winget (gestor de paquetes de Windows)"

try {
    $null = Get-Command winget -ErrorAction Stop
    Write-Ok "winget disponible"
} catch {
    Write-Err "winget no esta disponible."
    Write-Host "    Actualiza Windows o instala 'App Installer' desde Microsoft Store."
    exit 1
}

# ================================================================
# 2. Instalar dependencias
# ================================================================
function Install-IfMissing($name, $cmd, $wingetId) {
    if (Get-Command $cmd -ErrorAction SilentlyContinue) {
        Write-Ok "$name ya instalado"
        return
    }
    Write-Host "  Instalando $name..."
    winget install --id $wingetId --accept-package-agreements --accept-source-agreements --silent -e
    if ($LASTEXITCODE -ne 0) {
        Write-Err "Fallo la instalacion de $name. Instalalo manualmente y relanza."
        exit 1
    }
    Write-Ok "$name instalado"
}

Write-Step "Verificando Google Chrome"
Install-IfMissing "Chrome" "chrome.exe" "Google.Chrome"

Write-Step "Verificando Node.js LTS"
Install-IfMissing "Node.js" "node" "OpenJS.NodeJS.LTS"

Write-Step "Verificando Git"
Install-IfMissing "Git" "git" "Git.Git"

# Refrescar PATH para que node/git/npm esten disponibles en esta sesion
$env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" +
            [System.Environment]::GetEnvironmentVariable("Path","User")

# Verificar que node y npm funcionan despues del refresco
Write-Step "Verificando node y npm"
$nodeVer = node --version 2>$null
$npmVer = npm --version 2>$null
if ($LASTEXITCODE -ne 0 -or -not $nodeVer) {
    Write-Err "node o npm no disponibles. Cerra esta ventana y abri una terminal nueva."
    Write-Host "    Los comandos van a funcionar despues de refrescar el PATH."
    exit 1
}
Write-Ok "node $nodeVer, npm $npmVer"

# ================================================================
# 3. Clonar QuantOrchestrator si no estamos dentro del repo
# ================================================================
Write-Step "Verificando QuantOrchestrator"

# Intentar detectar si estamos dentro del repo
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir

if (-not (Test-Path (Join-Path $repoRoot "opencode.json"))) {
    Write-Step "Clonando QuantOrchestrator"
    $cloneDir = Join-Path $env:USERPROFILE "Documents\Inversiones\QuantOrchestrator"

    if (Test-Path $cloneDir) {
        Write-Ok "Repo ya existe en $cloneDir (no se reclona)"
    } else {
        git clone https://github.com/maxidelfino/QuantOrchestrator.git $cloneDir
        if ($LASTEXITCODE -ne 0) { Write-Err "git clone fallo."; exit 1 }
        Write-Ok "Clonado en $cloneDir"
    }
    $repoRoot = $cloneDir
} else {
    Write-Ok "Ejecutando desde dentro del repo: $repoRoot"
}

# ================================================================
# 4. Clonar el MCP de TradingView
# ================================================================
$mcpDir = Join-Path $repoRoot "tradingview-mcp"

Write-Step "Descargando el servidor MCP (tradingview-mcp-jackson)"
if (Test-Path $mcpDir) {
    Write-Ok "Repo ya existe en $mcpDir (no se reclona)"
} else {
    git clone https://github.com/LewisWJackson/tradingview-mcp-jackson.git $mcpDir
    if ($LASTEXITCODE -ne 0) { Write-Err "git clone fallo."; exit 1 }
    Write-Ok "Clonado en $mcpDir"
}

# ================================================================
# 5. npm install
# ================================================================
Write-Step "Instalando dependencias del MCP (npm install)"

Push-Location $mcpDir
try {
    & npm install --silent 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "npm install tuvo warnings, reintentando sin --silent..."
        & npm install
        if ($LASTEXITCODE -ne 0) { Write-Err "npm install fallo."; exit 1 }
    }
    Write-Ok "Dependencias instaladas"

    # Copiar rules.example.json a rules.json si no existe
    $rulesPath = Join-Path $mcpDir "rules.json"
    $rulesExample = Join-Path $mcpDir "rules.example.json"
    if (-not (Test-Path $rulesPath) -and (Test-Path $rulesExample)) {
        Copy-Item $rulesExample $rulesPath
        Write-Ok "rules.json creado (personalizalo con tu watchlist y criterios)"
    }
} finally {
    Pop-Location
}

# ================================================================
# 6. Crear launch-tv.bat en el Escritorio
# ================================================================
Write-Step "Creando launcher de Chrome en el Escritorio"

$desktop = [Environment]::GetFolderPath("Desktop")
$launcherPath = Join-Path $desktop "launch-tv.bat"

# Detectar ruta real de chrome.exe
$chromeCandidates = @(
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
)
$chromeExe = $chromeCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $chromeExe) {
    Write-Warn "No se encontro chrome.exe. El .bat usara la ruta por defecto; editalo si falla."
    $chromeExe = "$env:ProgramFiles\Google\Chrome\Application\chrome.exe"
}

# Tambien copiar a scripts/launch-tv.bat dentro del repo
$repoLauncher = Join-Path $repoRoot "scripts\launch-tv.bat"

$bat = @"
@echo off
REM Launcher de Chrome con CDP para TradingView + QuantOrchestrator.
REM Deja esta ventana abierta mientras uses el MCP.

set CHROME="$chromeExe"
set PROFILE=%USERPROFILE%\tv-cdp-profile

start "" %CHROME% --remote-debugging-port=9222 --user-data-dir="%PROFILE%" --no-first-run --no-default-browser-check "https://www.tradingview.com/chart/"
"@
Set-Content -Path $launcherPath -Value $bat -Encoding ASCII
Write-Ok "Creado: $launcherPath"

# ================================================================
# 7. Configuracion de OpenCode (opencode.json)
# ================================================================
Write-Step "Verificando configuracion de OpenCode"

$opencodePath = Join-Path $repoRoot "opencode.json"
if (Test-Path $opencodePath) {
    $opencode = Get-Content $opencodePath -Raw | ConvertFrom-Json
    if ($opencode.mcp.tradingview) {
        Write-Ok "opencode.json ya tiene la entrada 'tradingview' en mcp"
    } else {
        Write-Warn "opencode.json no tiene la entrada MCP. Verificando manualmente..."
        Write-Host "    La entrada deberia ser:"
        Write-Host '    "mcp": { "tradingview": { "command": ["node", "./tradingview-mcp/src/server.js"], "type": "local" } }'
    }
} else {
    Write-Err "No se encontro opencode.json en $repoRoot"
}

# ================================================================
# 8. Resumen final
# ================================================================
Write-Host ""
Write-Host "================================================" -ForegroundColor Green
Write-Host "  Setup de TradingView MCP completado" -ForegroundColor Green
Write-Host "================================================" -ForegroundColor Green
Write-Host ""
Write-Host "Proximos pasos (requieren tu accion):"
Write-Host ""
Write-Host "  1. Instalar OpenCode globalmente si no lo tenes:"
Write-Host "     npm install -g opencode-ai"
Write-Host ""
Write-Host "  2. Instalar gentle-ai globalmente:"
Write-Host "     Ver https://github.com/Gentleman-Programming/gentle-ai"
Write-Host ""
Write-Host "  3. Doble clic en 'launch-tv.bat' (esta en tu Escritorio)."
Write-Host "     Se abre Chrome con TradingView."
Write-Host "     Logueate (solo la primera vez)."
Write-Host ""
Write-Host "  4. Abrir OpenCode dentro del repo QuantOrchestrator:"
Write-Host "     cd $repoRoot"
Write-Host "     opencode"
Write-Host ""
Write-Host "  5. Verificar conexion: escribi en OpenCode:"
Write-Host "     Usa tv_health_check"
Write-Host ""
Write-Host "  Deberias ver: cdp_connected: true"
Write-Host ""