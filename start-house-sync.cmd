@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Setting up the Python environment...
  uv sync --frozen --extra dev --python 3.12 --cache-dir .cache\uv
  if errorlevel 1 goto failed
)
if not exist "node_modules\three\build\three.module.js" (
  echo Setting up the browser viewer...
  call npm.cmd ci --cache .cache\npm
  if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" -m housesync.server --open
if errorlevel 1 goto failed
exit /b 0
:failed
echo.
echo Setup did not complete. See the message above and README.md.
pause
exit /b 1
