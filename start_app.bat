@echo off
setlocal
set "APP_DIR=%~dp0"
if exist "%APP_DIR%.venv\Scripts\python.exe" (
  set "PYTHON_EXE=%APP_DIR%.venv\Scripts\python.exe"
) else if exist "%APP_DIR%..\.venv\Scripts\python.exe" (
  set "PYTHON_EXE=%APP_DIR%..\.venv\Scripts\python.exe"
) else (
  set "PYTHON_EXE=python"
)
cd /d "%APP_DIR%"
"%PYTHON_EXE%" "%APP_DIR%run_app.py"
pause
