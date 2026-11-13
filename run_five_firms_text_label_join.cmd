@echo off
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%~dp0src"
set "pilot_python=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if not exist "%pilot_python%" (
    echo JOIN_FAIL PYTHON:NOT_FOUND
    echo Press any key to close this window.
    pause >nul
    exit /b 1
)
"%pilot_python%" "%~dp0scripts\check_five_firms_text_label_join.py"
set "join_exit=%ERRORLEVEL%"
echo Press any key to close this window.
pause >nul
exit /b %join_exit%
