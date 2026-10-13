@echo off
REM SANGAM WhatsApp bridge - double-click to start. Keep this window open during the demo.
cd /d "%~dp0wa_bridge"
set PUPPETEER_SKIP_DOWNLOAD=true
if not exist node_modules (
  echo First run: installing bridge packages ^(1-2 minutes^)...
  call npm install --no-audit --no-fund
)
node index.js
pause
