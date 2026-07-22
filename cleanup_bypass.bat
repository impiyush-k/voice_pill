@echo off
:: Launches the companion PowerShell bypass script with the -Remove flag to clean up routes.

cd /d "%~dp0"
echo ========================================================
echo  Voice Pill - Cleaning up VPN Route Bypass (Elevated)
echo ========================================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File .\route_groq_bypass_vpn.ps1 -Remove

echo.
echo ========================================================
echo  Press any key to close this window...
echo ========================================================
pause > nul
