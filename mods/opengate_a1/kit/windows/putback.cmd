@echo off
rem ILCE-6400 open-gate kit: putback. See ..\README.md.
setlocal
cd /d "%~dp0..\..\..\.."
set "PY=py -3"
if exist "venv\Scripts\python.exe" set "PY=venv\Scripts\python.exe"
%PY% -m tools.kits.opengate_a6400 putback %*
set "RC=%ERRORLEVEL%"
echo.
pause
exit /b %RC%
