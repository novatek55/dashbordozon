@echo off
setlocal

cd /d "%~dp0"

if not exist ".\.venv\Scripts\python.exe" (
  echo [ERROR] Python venv not found: .\.venv\Scripts\python.exe
  echo Create venv and install requirements first.
  pause
  exit /b 1
)

".\.venv\Scripts\python.exe" ".\sync_now.py" --python ".\.venv\Scripts\python.exe" %*
set EXIT_CODE=%ERRORLEVEL%

if not "%EXIT_CODE%"=="0" (
  echo [ERROR] sync_now failed with exit code %EXIT_CODE%
  pause
  exit /b %EXIT_CODE%
)

echo [OK] sync_now completed
endlocal
