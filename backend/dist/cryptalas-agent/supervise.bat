@echo off
REM CryptALAS agent supervisor — never dies. Swaps in self-updates, restarts
REM on any exit. Use it instead of running cryptalas.exe directly.
:loop
if exist "%~dp0cryptalas.exe.new" move /Y "%~dp0cryptalas.exe.new" "%~dp0cryptalas.exe" >nul
"%~dp0cryptalas.exe" %*
echo.
echo cryptalas exited (code %ERRORLEVEL%), restarting in 3s...
timeout /t 3 /nobreak >nul
goto loop
