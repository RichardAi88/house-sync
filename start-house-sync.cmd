@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -Command "try { $taskHealth = Invoke-RestMethod http://127.0.0.1:8766/api/health -TimeoutSec 2; if ($taskHealth.application -eq 'House Sync' -and $taskHealth.features -contains 'revision_comparison') { exit 0 } } catch {}; exit 1" >nul 2>&1
if not errorlevel 1 (
  start "" "http://127.0.0.1:8766"
  exit /b 0
)
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
