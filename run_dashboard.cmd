@echo off
REM Starts the FinOps API + dashboard locally (SYNTHETIC mock data, no cloud access).
REM Open http://127.0.0.1:8765/dashboard  -  close this window to stop the server.
cd /d "%~dp0"
set FINOP_MOCK_MODE=true
.venv\Scripts\python.exe -c "import uvicorn, fastapi, pandas, sklearn" >nul 2>nul
if errorlevel 1 (
  echo Setup is not finished. Double-click run_checks.cmd, wait until its window says
  echo "Finished", then try again. It needs internet the first time and takes 5-15 minutes.
  pause
  exit /b 1
)
echo Dashboard: http://127.0.0.1:8765/dashboard   (close this window to stop)
.venv\Scripts\python.exe -m uvicorn finops_engine.api.app:app --host 127.0.0.1 --port 8765
pause
