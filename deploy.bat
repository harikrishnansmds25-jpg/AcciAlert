@echo off
setlocal
if "%~1"=="" (
  echo Usage: deploy.bat https://new-random.trycloudflare.com
  exit /b 2
)
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy.ps1" -TunnelUrl "%~1"
exit /b %ERRORLEVEL%
