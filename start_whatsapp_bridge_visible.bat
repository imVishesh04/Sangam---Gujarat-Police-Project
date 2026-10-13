@echo off
REM Same as start_whatsapp_bridge.bat, but opens a real WhatsApp Web window to scan.
REM Use this if linking fails or the QR in the black window is hard to scan.
cd /d "%~dp0wa_bridge"
set PUPPETEER_SKIP_DOWNLOAD=true
set WA_SHOW_BROWSER=1
if not exist node_modules (
  echo First run: installing bridge packages ^(1-2 minutes^)...
  call npm install --no-audit --no-fund
)
node index.js
pause
