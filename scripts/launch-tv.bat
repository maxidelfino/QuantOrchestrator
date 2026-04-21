@echo off
REM ================================================================
REM  launch-tv.bat — TradingView + Chrome CDP Launcher
REM  Para usar: doble clic o correr desde cmd/PowerShell.
REM  Deja esta ventana abierta mientras uses el MCP.
REM ================================================================

set PORT=9222

REM ----- Detectar chrome.exe -----
set "CHROME="

if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" (
    set "CHROME=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
)
if not defined CHROME (
    if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" (
        set "CHROME=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
    )
)
if not defined CHROME (
    if exist "%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe" (
        set "CHROME=%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"
    )
)

if not defined CHROME (
    echo.
    echo [ERROR] No se encontro chrome.exe.
    echo.
    echo Rutas probadas:
    echo   %ProgramFiles%\Google\Chrome\Application\chrome.exe
    echo   %ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe
    echo   %LOCALAPPDATA%\Google\Chrome\Application\chrome.exe
    echo.
    echo Instala Google Chrome desde https://www.google.com/chrome/
    echo O edita este .bat y setea CHROME manualmente.
    echo.
    pause
    exit /b 1
)

echo.
echo [OK] Chrome detectado en: %CHROME%
echo.

REM ----- Perfil dedicado -----
set "PROFILE=%USERPROFILE%\tv-cdp-profile"
if not exist "%PROFILE%" mkdir "%PROFILE%"

echo [OK] Perfil CDP: %PROFILE%
echo.

REM ----- Matar instancias previas de Chrome en puerto 9222 -----
echo Cerrando instancias previas de Chrome CDP en puerto %PORT%...
tasklist /FI "IMAGENAME eq chrome.exe" 2>nul | findstr /I "chrome.exe" >nul
if %errorlevel% equ 0 (
    REM Intentar cerrar solo instancias con CDP
    curl -s http://localhost:%PORT%/json/version >nul 2>&1
    if %errorlevel% equ 0 (
        echo Ya hay una instancia CDP en puerto %PORT%. Cerrandola...
        for /f "tokens=2" %%a in ('tasklist /FI "IMAGENAME eq chrome.exe" /FO LIST ^| findstr "PID" 2^>nul') do (
            taskkill /PID %%a /F >nul 2>&1
        )
        timeout /t 2 /nobreak >nul
    )
)

REM ----- Lanzar Chrome con CDP -----
echo Lanzando Chrome con CDP en puerto %PORT%...
echo.
echo ================================================================
echo   TradingView + CDP listo
echo   NO CIERRES ESTA VENTANA mientras uses el MCP.
echo ================================================================
echo.

start "" "%CHROME%" --remote-debugging-port=%PORT% --user-data-dir="%PROFILE%" --no-first-run --no-default-browser-check "https://www.tradingview.com/chart/"

REM ----- Esperar y verificar CDP -----
echo Esperando que CDP levante...
timeout /t 5 /nobreak >nul

:check
curl -s http://localhost:%PORT%/json/version >nul 2>&1
if %errorlevel% neq 0 (
    echo Aun esperando...
    timeout /t 2 /nobreak >nul
    goto check
)

echo.
echo [OK] CDP listo en http://localhost:%PORT%
echo.
echo Si es la primera vez, logueate en TradingView en la ventana de Chrome.
echo Despues, en Claude Code/OpenCode escribi: "Usa tv_health_check"
echo.