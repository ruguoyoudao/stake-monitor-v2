@echo off
chcp 65001 >nul 2>&1
echo ============================================================
echo   Stake.com Monitor - Startup Script
echo ============================================================
echo.

:: Kill existing Edge
echo [1/3] Closing existing Edge...
taskkill /F /IM msedge.exe >nul 2>&1
timeout /t 3 /nobreak >nul

:: Start Edge with CDP debug port
echo [2/3] Starting Edge with CDP (port 9222)...
start "" "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" --remote-debugging-port=9222 https://stake.com/sports/live
timeout /t 5 /nobreak >nul

:: Run monitor
echo [3/3] Starting monitor...
echo.
echo Login to stake.com in the Edge window, then monitoring begins.
echo Press Ctrl+C to stop.
echo.
python run_monitor.py

pause
