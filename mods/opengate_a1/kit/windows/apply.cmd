@echo off
rem ILCE-6400 open-gate kit: apply. See ..\README.md.
setlocal
cd /d "%~dp0..\..\..\.."
set "PY=py -3"
if exist "venv\Scripts\python.exe" set "PY=venv\Scripts\python.exe"
set "MOD=%~1"
if "%MOD%"=="" set "MOD=artifacts\tests\opengate-a1\OpenGateA1.mod"
%PY% -m tools.kits.opengate_a6400 apply --mod "%MOD%"
set "RC=%ERRORLEVEL%"
echo.
pause
exit /b %RC%
