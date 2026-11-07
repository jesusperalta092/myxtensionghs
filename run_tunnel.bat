@echo off
set "SCRIPT_DIR=%~dp0"
if exist "%SCRIPT_DIR%cf_url.txt" del /f /q "%SCRIPT_DIR%cf_url.txt" >nul 2>&1

set "CF_BIN=C:\Users\USER\Downloads\Cloudflare-heramienta\cloudflared.exe"
if not exist "%CF_BIN%" (
    set "CF_BIN=%SCRIPT_DIR%..\Cloudflare-heramienta\cloudflared.exe"
)
if not exist "%CF_BIN%" (
    set "CF_BIN=cloudflared.exe"
)

"%CF_BIN%" tunnel --url http://localhost:%1 --http-host-header localhost > "%SCRIPT_DIR%cf_url.txt" 2>&1
