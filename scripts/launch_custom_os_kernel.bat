@echo off
setlocal
cd /d "%~dp0.."
set "ROOT=%CD%\"

set "PY_EXE="
if exist "C:\Python314\python.exe" (
    set "PY_EXE=C:\Python314\python.exe"
) else if exist "%ROOT%venv\Scripts\python.exe" (
    set "PY_EXE=%ROOT%venv\Scripts\python.exe"
) else (
    set "PY_EXE=python"
)

if "%~1"=="" (
    "%PY_EXE%" -u "%ROOT%custom_os\launcher\launch_custom_kernel_flow.py" --mode deadman=60
) else (
    "%PY_EXE%" -u "%ROOT%custom_os\launcher\launch_custom_kernel_flow.py" %*
)
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
    echo.
    echo [ERROR] Custom OS kernel launcher exited with code %RC%.
    pause
    exit /b %RC%
)
exit /b 0
