@echo off
setlocal

cd /d "%~dp0"

call D:\miniconda3\Scripts\activate.bat quicktodoadder
if errorlevel 1 (
  echo Failed to activate conda environment: quicktodoadder
  pause
  exit /b 1
)

start "QuickTodoAdder" "%CONDA_PREFIX%\pythonw.exe" "%~dp0quick_todo_adder.py" --config "%~dp0config.json"
exit /b 0
