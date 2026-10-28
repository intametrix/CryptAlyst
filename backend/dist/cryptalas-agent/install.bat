@echo off
REM CryptALAS agent - one-command installer (Windows). No Go needed.
REM   install.bat                install + start (zero config)
REM   install.bat gpu-1 cuda     install with name + device
REM   install.bat gpu-1 cuda http://platform:8787
setlocal
set NAME=%1
set DEVICE=%2
set REGISTER=%3
if "%DEVICE%"=="" set DEVICE=auto

set IP=%LOCALAPPDATA%\CryptALAS
mkdir "%IP%" 2>nul
copy /Y "%~dp0cryptalas.exe" "%IP%\cryptalas.exe" >nul
if exist "%~dp0gpu" xcopy /E /I /Y "%~dp0gpu" "%IP%\gpu" >nul

set ARGS=-device %DEVICE%
if not "%NAME%"=="" set ARGS=%ARGS% -name %NAME%
if not "%REGISTER%"=="" set ARGS=%ARGS% -register %REGISTER%

REM supervisor loop: swap self-updates + restart forever
(
echo @echo off
echo :loop
echo if exist "%IP%\cryptalas.exe.new" move /Y "%IP%\cryptalas.exe.new" "%IP%\cryptalas.exe" ^>nul
echo "%IP%\cryptalas.exe" %ARGS%
echo echo cryptalas exited, restarting...
echo timeout /t 2 /nobreak ^>nul
echo goto loop
) > "%IP%\supervise.bat"

schtasks /Create /TN "CryptALAS Agent" /TR "\"%IP%\supervise.bat\"" /SC ONLOGON /RL LIMITED /F >nul
schtasks /Run /TN "CryptALAS Agent" >nul

echo done - agent installed and running.
echo   name: %NAME%   device: %DEVICE%
echo   status: type "%IP%\agent.log"
echo   uninstall: schtasks /Delete /TN "CryptALAS Agent" /F
endlocal
