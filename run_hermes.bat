@echo off
chcp 65001 > nul
title Hermes Agent (GHS Bridge Mode)
echo =======================================================
echo   ⚡ Hermes Agent (Nous Research) + GHS Engine
echo   Provider: Custom OpenAI-Compatible (Local Bridge)
echo   Endpoint: http://127.0.0.1:17614/v1
echo =======================================================
cd /d "%~dp0hermes-agent(mod)"
python cli.py %*
