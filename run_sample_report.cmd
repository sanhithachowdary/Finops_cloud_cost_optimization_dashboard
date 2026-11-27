@echo off
REM Runs the engine on every CSV in data\demo\ and writes output\sample-report\report.html
REM (plus report.md and report.json), then opens the HTML report.
cd /d "%~dp0"
.venv\Scripts\python.exe -c "import uvicorn, fastapi, pandas, sklearn" >nul 2>nul
if errorlevel 1 (
  echo Setup is not finished. Double-click run_checks.cmd, wait until its window says
  echo "Finished", then try again. It needs internet the first time and takes 5-15 minutes.
  pause
  exit /b 1
)
.venv\Scripts\python.exe -m scripts.sample_data_report %*
if errorlevel 1 (
  pause
  exit /b 1
)
start "" "output\sample-report\report.html"
pause
