# Nehanth MT5 Bridge Server — PowerShell Launcher
# Run: .\start_trading.ps1

$Host.UI.RawUI.WindowTitle = "Nehanth Trading Terminal"

Write-Host ""
Write-Host "  ================================================================" -ForegroundColor Cyan
Write-Host "       Nehanth MT5 Bridge Server - Starting Up" -ForegroundColor Cyan
Write-Host "  ================================================================" -ForegroundColor Cyan
Write-Host ""

# Check Python
try {
    $pyVersion = python --version 2>&1
    Write-Host "  Python: $pyVersion" -ForegroundColor Green
} catch {
    Write-Host "  [ERROR] Python not found. Install from https://python.org" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

# Navigate to script directory
Set-Location $PSScriptRoot

# Install packages
Write-Host ""
Write-Host "  [1/3] Installing Python packages..." -ForegroundColor Yellow
pip install fastapi uvicorn websockets MetaTrader5 python-dotenv pydantic --quiet 2>$null
Write-Host "         Done." -ForegroundColor Green

# Check .env
if (-not (Test-Path ".env")) {
    Write-Host "  [ERROR] .env file not found!" -ForegroundColor Red
    Write-Host "          Copy .env.example to .env and add your MT5 credentials." -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

# Check MT5 is running
$mt5Process = Get-Process -Name "terminal64" -ErrorAction SilentlyContinue
if (-not $mt5Process) {
    Write-Host ""
    Write-Host "  [WARNING] MetaTrader 5 terminal not detected!" -ForegroundColor Yellow
    Write-Host "            Please start MT5 and log in before continuing." -ForegroundColor Yellow
    Write-Host ""
}

Write-Host ""
Write-Host "  [2/3] Starting API server on http://localhost:8500 ..." -ForegroundColor Yellow
Write-Host ""
Write-Host "  ================================================================" -ForegroundColor Cyan
Write-Host "   Server:    http://localhost:8500" -ForegroundColor White
Write-Host "   API Docs:  http://localhost:8500/docs" -ForegroundColor White
Write-Host "   Health:    http://localhost:8500/api/health" -ForegroundColor White
Write-Host "  ================================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  [3/3] Opening dashboard in browser..." -ForegroundColor Yellow

# Open dashboard served from the API server (avoids file:// CORS issues)
Start-Process "http://localhost:8500"

Write-Host ""
Write-Host "  Dashboard opened! Press Ctrl+C to stop the server." -ForegroundColor Green
Write-Host ""

# Start server
python mt5_api_server.py
