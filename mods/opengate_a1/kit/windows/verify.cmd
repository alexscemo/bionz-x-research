@echo off
rem ILCE-6400 open-gate kit: verify. See ..\README.md.
setlocal
cd /d "%~dp0..\..\..\.."
set "PY=py -3"
if exist "venv\Scripts\python.exe" set "PY=venv\Scripts\python.exe"
%PY% -m tools.kits.opengate_a6400 verify %*
set "RC=%ERRORLEVEL%"
echo.
pause
exit /b %RC%
