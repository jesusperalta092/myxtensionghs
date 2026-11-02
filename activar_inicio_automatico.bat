@echo off
chcp 65001 >nul
title GHS - Activar Inicio Automatico
cd /d "%~dp0"

echo.
echo ==============================================================
echo    GHS AGENTE AUTONOMO - ACTIVAR INICIO AUTOMATICO SILENCIOSO
echo ==============================================================
echo.
echo Este asistente configurara a GHS para que se inicie automaticamente
echo cada vez que enciendas tu computadora, 100%% en segundo plano,
echo sin ventanas negras y con poderes totales sobre tu maquina.
echo.

set "SCRIPT_DIR=%~dp0"
REM Quitar barra invertida final si existe
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

set "BAT_PATH=%SCRIPT_DIR%\start.bat"
if not exist "%BAT_PATH%" (
    echo [ERROR] No se encontro start.bat en esta carpeta.
    echo Asegurate de ejecutar este archivo junto a start.bat.
    pause
    exit /b 1
)

set "STARTUP_DIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "VBS_STARTUP=%STARTUP_DIR%\GHS_Agent_Bridge.vbs"
set "VBS_LOCAL=%SCRIPT_DIR%\iniciar_en_segundo_plano.vbs"

REM Generar VBS silencioso local
(
    echo Set WshShell = CreateObject^("WScript.Shell"^)
    echo WshShell.CurrentDirectory = "%SCRIPT_DIR%"
    echo WshShell.Run "cmd.exe /c """ ^& "%BAT_PATH%" ^& """", 0, False
    echo Set WshShell = Nothing
) > "%VBS_LOCAL%"

REM Copiar a la carpeta de Inicio de Windows
copy /y "%VBS_LOCAL%" "%VBS_STARTUP%" >nul

echo [1/2] Lanzador silencioso creado:
echo       %VBS_LOCAL%
echo.
echo [2/2] Instalado en Inicio de Windows:
echo       %VBS_STARTUP%
echo.
echo ==============================================================
echo   INICIO AUTOMATICO ACTIVADO EXITOSAMENTE
echo ==============================================================
echo.
echo A partir de ahora:
echo  1. Cada vez que enciendas tu PC, el agente arrancara solo e invisible.
echo  2. Si no tienes Python instalado, lo descargara e instalara en silencio.
echo  3. En tu navegador, el boton de GHS siempre estara verde y listo.
echo.
echo Deseas iniciar el agente AHORA MISMO en segundo plano? (S/N)
set /p AHORA="> "
if /i "%AHORA%"=="S" (
    wscript.exe "%VBS_LOCAL%"
    echo.
    echo [OK] Agente iniciado en segundo plano. Ya puedes abrir tu navegador.
    timeout /t 3 >nul
)

exit /b 0
