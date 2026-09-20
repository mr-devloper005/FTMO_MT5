@echo off
title Nehanth Trading Terminal - MT5 Bridge
color 0A

echo.
echo  ================================================================
echo       Nehanth MT5 Bridge Server - Starting Up
echo  ================================================================
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python is not installed or not in PATH.
    echo         Download from: https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)

:: Navigate to script directory
cd /d "%~dp0"

:: Install required packages
echo [1/4] Installing required Python packages...
pip install fastapi uvicorn websockets MetaTrader5 python-dotenv pydantic --quiet 2>nul
if errorlevel 1 (
    echo [WARNING] Some packages may have failed to install. Trying anyway...
)
echo       Done.
echo.

:: Check .env file
if not exist .env (
    echo [ERROR] .env file not found!
    echo         Copy .env.example to .env and fill in your MT5 credentials.
    echo.
    pause
    exit /b 1
)

echo [2/4] Loading credentials from .env file...
echo       Done.
echo.

echo [3/4] Starting MT5 API Bridge Server on port 8500...
echo.
echo  ================================================================
echo   DASHBOARD: http://localhost:8500
echo   API Docs:  http://localhost:8500/docs
echo  ================================================================
echo.
echo   IMPORTANT: Make sure MetaTrader 5 terminal is running!
echo.
echo [4/4] Opening dashboard in browser...
start http://localhost:8500
echo.
echo   Press Ctrl+C to stop the server.
echo  ================================================================
echo.

:: Start server
python mt5_api_server.py

:: If server exits
echo.
echo [!] Server stopped.
pause