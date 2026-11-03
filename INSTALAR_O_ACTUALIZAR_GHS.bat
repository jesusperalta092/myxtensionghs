:: SPDX-License-Identifier: GPL-3.0-or-later
@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
title GHS - Instalador y Actualizador Autónomo
cd /d "%~dp0"

echo.
echo  ============================================================
echo     ⚡ GHS AUTONOMOUS AGENT - INSTALADOR ^& ACTUALIZADOR ⚡
echo  ============================================================
echo.

set "REPO_URL=https://github.com/jesusperalta092/myxtensionghs.git"
set "ZIP_URL=https://github.com/jesusperalta092/myxtensionghs/archive/refs/heads/main.zip"
set "TARGET_DIR=%USERPROFILE%\Downloads\GHS-Script-Free"

:: Caso 1: Si este BAT ya se encuentra dentro de la carpeta del proyecto
if exist "%~dp0bridge.py" (
    echo   [+] Detectada instalacion existente en: "%~dp0"
    echo   [+] Comprobando actualizaciones en GitHub...
    where git >nul 2>nul
    if not errorlevel 1 (
        git pull origin main
        echo   [✓] Actualizacion sincronizada exitosamente.
    ) else (
        echo   [i] Git no detectado en PATH. Continuando con los archivos actuales.
    )
    echo.
    echo   [+] Iniciando el puente supremo de GHS...
    call "%~dp0start.bat"
    exit /b 0
)

:: Caso 2: El usuario ejecuto este BAT de manera individual (instalacion limpia)
echo   [1/3] Preparando directorio en: "%TARGET_DIR%"
if not exist "%TARGET_DIR%" mkdir "%TARGET_DIR%" >nul 2>nul

echo   [2/3] Obteniendo el arsenal completo de GHS desde GitHub...
where git >nul 2>nul
if not errorlevel 1 (
    echo         Usando Git para clonar el repositorio oficial...
    git clone %REPO_URL% "%TARGET_DIR%"
) else (
    echo         Git no encontrado. Descargando paquete completo con PowerShell...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; $zip = Join-Path $env:TEMP 'ghs_main.zip'; (New-Object System.Net.WebClient).DownloadFile('%ZIP_URL%', $zip); Expand-Archive -Path $zip -DestinationPath $env:TEMP\ghs_extracted -Force; Copy-Item -Path \"$env:TEMP\ghs_extracted\myxtensionghs-main\*\" -Destination '%TARGET_DIR%' -Recurse -Force; Remove-Item $zip -Force; Remove-Item \"$env:TEMP\ghs_extracted\" -Recurse -Force"
)

if not exist "%TARGET_DIR%\bridge.py" (
    echo.
    echo   [ERROR] No se pudo descargar el arsenal. Verifica tu conexion a internet.
    pause
    exit /b 1
)

echo   [✓] Arsenal completo descargado exitosamente.
echo.
echo   [3/3] Abriendo la carpeta de la extension para tu navegador...
echo         (Carga esta carpeta en chrome://extensions en Modo Desarrollador)
start explorer.exe "%TARGET_DIR%\1-EXTENSION-CLIENTE"

echo.
echo   ============================================================
echo     Iniciando el Puente Local de GHS (start.bat)...
echo   ============================================================
echo.
cd /d "%TARGET_DIR%"
call "%TARGET_DIR%\start.bat"
exit /b 0
