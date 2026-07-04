@echo off
chcp 65001 >nul
echo در حال خاموش کردن برنامه...
cd /d "%~dp0"
docker compose down
echo برنامه خاموش شد.
pause
