@echo off
chcp 65001 >nul
title GHS - Desactivar Inicio Automatico
cd /d "%~dp0"

echo.
echo ==============================================================
echo   GHS AGENTE AUTONOMO - DESACTIVAR INICIO AUTOMATICO
echo ==============================================================
echo.

set "STARTUP_DIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "VBS_STARTUP=%STARTUP_DIR%\GHS_Agent_Bridge.vbs"

if exist "%VBS_STARTUP%" (
    del /f /q "%VBS_STARTUP%" >nul 2>nul
    echo [OK] Acceso directo eliminado de Inicio de Windows.
) else (
    echo [INFO] El inicio automatico ya estaba desactivado.
)

REM Comprobar si hay un bridge corriendo en el puerto 17613
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :17613 ^| findstr LISTENING 2^>nul') do (
    echo Deteniendo proceso de GHS Bridge (pid %%a)...
    taskkill /F /PID %%a >nul 2>nul
)

echo.
echo ==============================================================
echo   INICIO AUTOMATICO DESACTIVADO EXITOSAMENTE
echo ==============================================================
echo.
pause
exit /b 0
