@echo off
setlocal

echo [1/3] Disabling WinINET proxy for current user...
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings" /v ProxyEnable /t REG_DWORD /d 0 /f >nul
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings" /v ProxyServer /f >nul 2>&1
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings" /v AutoConfigURL /f >nul 2>&1

echo [2/3] Resetting WinHTTP proxy (requires Administrator)...
net session >nul 2>&1
if errorlevel 1 (
  echo [WARN] Not running as Administrator. WinHTTP reset skipped.
  echo        Run this file as Administrator to fully restore direct access.
) else (
  netsh winhttp reset proxy
)

echo [3/3] Current status:
reg query "HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings" /v ProxyEnable
netsh winhttp show proxy

echo.
echo Done. Standard split/direct mode is restored.
endlocal
