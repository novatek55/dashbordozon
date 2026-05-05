@echo off
chcp 65001 >nul 2>&1
set "HERE=%~dp0"

echo Restarting dashboard...
powershell -NoProfile -ExecutionPolicy Bypass -File "%HERE%restart_dashboard.ps1"
set "EXITCODE=%ERRORLEVEL%"

if not "%EXITCODE%"=="0" (
  echo Dashboard restart failed with exit code %EXITCODE%.
  exit /b %EXITCODE%
)

echo Dashboard is available at http://127.0.0.1:8088/
