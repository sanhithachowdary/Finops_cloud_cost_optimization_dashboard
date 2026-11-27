@echo off
REM One click: start the dashboard with the sample-data settings, load the Q2 sample bill
REM (data\demo\focus_compliant_demo_q2.csv) and open it in your browser.
REM Close any other dashboard window first. Close the "FinOps dashboard" window to stop.
cd /d "%~dp0"
.venv\Scripts\python.exe -c "import uvicorn, fastapi, pandas, sklearn" >nul 2>nul
if errorlevel 1 (
  echo Setup is not finished. Double-click run_checks.cmd, wait until its window says
  echo "Finished", then try again. It needs internet the first time and takes 5-15 minutes.
  pause
  exit /b 1
)
.venv\Scripts\python.exe -c "import socket; socket.create_connection(('127.0.0.1', 8765), 2).close()" >nul 2>nul
if not errorlevel 1 (
  echo A dashboard is already running on port 8765.
  echo Close its window ^(titled "Dashboard" or "FinOps dashboard"^), then run run_demo.cmd again.
  pause
  exit /b 1
)
set FINOP_MOCK_MODE=true
set FINOP_ENGINE_CONFIG=config\finops-engine.sample.json
set DEMO_FILE=%~1
if "%DEMO_FILE%"=="" set DEMO_FILE=data\demo\focus_compliant_demo_q2.csv
start "FinOps dashboard (close this window to stop)" .venv\Scripts\python.exe -m uvicorn finops_engine.api.app:app --host 127.0.0.1 --port 8765
.venv\Scripts\python.exe -m scripts.utilization_from_billing "%DEMO_FILE%" --post http://127.0.0.1:8765 --wait 60
if errorlevel 1 (
  pause
  exit /b 1
)
start "" "http://127.0.0.1:8765/dashboard?source=ingested&days=365"
echo.
echo Dashboard opened. To show another sample, close the dashboard window and drag that CSV onto run_demo.cmd.
pause
