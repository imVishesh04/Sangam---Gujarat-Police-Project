@echo off
REM Forget the linked WhatsApp session so a fresh QR is shown next time.
cd /d "%~dp0wa_bridge"
if exist .wa_session rmdir /s /q .wa_session
if exist .wwebjs_cache rmdir /s /q .wwebjs_cache
echo Bridge session cleared. Now run start_whatsapp_bridge_visible.bat and scan the new QR.
pause
