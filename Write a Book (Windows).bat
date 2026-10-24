@echo off
rem Double-click to write a book. The first run sets everything up (a minute or two).
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONUTF8=1

where py >nul 2>nul
if errorlevel 1 (
  echo Python is not installed. Download it from https://www.python.org/downloads/
  echo and tick "Add python.exe to PATH" when installing. Then try again.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Setting up for the first time...
  py -3 -m venv .venv || (echo Setup failed. & pause & exit /b 1)
)
".venv\Scripts\python.exe" -m pip install --quiet --upgrade -r requirements.txt || (echo Could not install the requirements. & pause & exit /b 1)

".venv\Scripts\python.exe" write_book.py %*
echo.
echo Finished.
pause
