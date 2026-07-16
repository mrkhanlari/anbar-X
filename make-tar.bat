@echo off
cd /d "%~dp0"
echo Building Docker image...
docker compose build
echo.
echo Exporting image to tar file...
docker save -o warehouse-app.tar anbar-x-warehouse:latest
echo.
echo Done! File saved: warehouse-app.tar
echo.
pause
