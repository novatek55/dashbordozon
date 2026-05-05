@echo off
setlocal

set PROXY=127.0.0.1:12334

echo [1/4] Setting WinINET proxy for current user to %PROXY%...
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings" /v ProxyEnable /t REG_DWORD /d 1 /f >nul
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings" /v ProxyServer /t REG_SZ /d %PROXY% /f >nul
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings" /v AutoConfigURL /f >nul 2>&1

echo [2/4] Refreshing proxy settings for apps...
rundll32 inetcpl.cpl,ClearMyTracksByProcess 8 >nul 2>&1

echo [3/4] Setting WinHTTP proxy (requires Administrator)...
net session >nul 2>&1
if errorlevel 1 (
  echo [WARN] Not running as Administrator. WinHTTP proxy skipped.
  echo        Run this file as Administrator to include Microsoft Store and services.
) else (
  netsh winhttp set proxy %PROXY%
)

echo [4/4] Current status:
reg query "HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings" /v ProxyEnable
reg query "HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings" /v ProxyServer
netsh winhttp show proxy

echo.
echo Done. All app traffic should go through %PROXY% when Hiddify is running.
endlocal
