@echo off
chcp 65001 >nul
echo ====================================
echo   راه‌اندازی انبارگردان
echo ====================================
echo.
cd /d "%~dp0"
docker compose up --build -d
echo.
echo برنامه روشن شد. می‌توانید از طریق آدرس زیر وارد شوید:
echo http://localhost:5000
echo یا از سایر کامپیوترها: http://192.168.1.88:5000
echo.
pause
