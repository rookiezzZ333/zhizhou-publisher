@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Python environment missing. Run the dependency installer first.
  pause
  exit /b 1
)
if "%~1"=="--check" (
  ".venv\Scripts\python.exe" -B -c "import httpx,markdown_it,yaml,PIL,playwright; print('DEPENDENCIES_OK')"
  exit /b
)
".venv\Scripts\python.exe" launcher.py
pause
