@echo off
:: This batch file exists because Windows allows right-clicking .bat files to "Run as administrator".
:: It automatically launches the companion PowerShell bypass script with the elevated permissions it needs.

cd /d "%~dp0"
echo ========================================================
echo  Voice Pill - Setting up VPN Route Bypass (Elevated)
echo ========================================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File .\route_groq_bypass_vpn.ps1

echo.
echo ========================================================
echo  Press any key to close this window...
echo ========================================================
pause > nul
