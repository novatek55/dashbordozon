@echo off
chcp 65001 >nul 2>&1
title SSH Tunnel -> newtekpro.ru/ozon/
echo Starting SSH tunnel to VDS (newtekpro.ru)...
echo Dashboard URL: https://newtekpro.ru/ozon/
echo.
echo Press Ctrl+C to stop
echo.
node "%~dp0ssh_tunnel.js"
pause
