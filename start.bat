@echo off
setlocal
cd /d "%~dp0"
for %%P in ("%~dp0data\desktop-venv\Scripts\python.exe" python py) do (
  "%%~P" -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>&1
  if not errorlevel 1 (
    "%%~P" scripts\launch.py
    goto :finished
  )
)
echo Python 3.10 or newer is required. Install Python and enable Add to PATH.
pause
exit /b 1

:finished
set "launch_status=%errorlevel%"
if not "%launch_status%"=="0" pause
exit /b %launch_status%
