@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    py -3 -m venv .venv
    if errorlevel 1 python -m venv .venv
    if errorlevel 1 goto error
)
if exist ".venv\requirements.installed" (
    fc /b requirements.txt ".venv\requirements.installed" >nul
    if not errorlevel 1 goto run
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto error
copy /y requirements.txt ".venv\requirements.installed" >nul
:run
".venv\Scripts\python.exe" main.py
if errorlevel 1 goto error
exit /b 0
:error
echo Khong the khoi dong. Kiem tra da cai Python 3.11+ va ket noi mang.
pause
exit /b 1
