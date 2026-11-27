@echo off
REM Drag a billing CSV onto this file (or: load_sample.cmd path\to\bill.csv).
REM Uploads the bill and the utilisation derived from its ResourceUtilizationPercent column
REM to the dashboard started by run_dashboard.cmd (http://127.0.0.1:8765). Nothing else is contacted.
cd /d "%~dp0"
if "%~1"=="" (
  echo Usage: drag a CSV file onto load_sample.cmd
  pause
  exit /b 1
)
.venv\Scripts\python.exe -c "import uvicorn, fastapi, pandas, sklearn" >nul 2>nul
if errorlevel 1 (
  echo Setup is not finished. Double-click run_checks.cmd, wait until its window says
  echo "Finished", then try again. It needs internet the first time and takes 5-15 minutes.
  pause
  exit /b 1
)
.venv\Scripts\python.exe -m scripts.utilization_from_billing "%~1" --post http://127.0.0.1:8765
pause
