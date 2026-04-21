@echo off
REM Wrapper que corre el setup de PowerShell saltando la execution policy.
REM Doble clic para setup automatico.

echo ================================================
echo  QuantOrchestrator - Setup TradingView MCP
echo ================================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup-tradingview.ps1"

echo.
pause