@echo off
REM Bootstrap for install-agent.ps1: Windows blocks unsigned scripts by default.
REM Usage:  install-agent.cmd [platform-url] [marketdata-url] [update-url]
setlocal
set PLATFORM=%~1
set MARKETDATA=%~2
set UPDATE=%~3
if "%PLATFORM%"==""   set PLATFORM=http://192.168.1.142:8787
if "%MARKETDATA%"=="" set MARKETDATA=http://192.168.1.142:8796
if "%UPDATE%"==""     set UPDATE=http://192.168.1.142:8899

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-agent.ps1" ^
  -Platform "%PLATFORM%" -Marketdata "%MARKETDATA%" -UpdateServer "%UPDATE%"
endlocal
