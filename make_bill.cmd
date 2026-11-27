@echo off
REM Asks for a period and a currency, then generates one bill for those dates into output\bills\
REM (plus a .labels.json file listing the planted anomalies). Upload the CSV on the dashboard
REM (Operations ^& validation tab) or drag it onto run_demo.cmd.
cd /d "%~dp0"
.venv\Scripts\python.exe -c "import uvicorn, fastapi, pandas, sklearn" >nul 2>nul
if errorlevel 1 (
  echo Setup is not finished. Double-click run_checks.cmd, wait until its window says
  echo "Finished", then try again. It needs internet the first time and takes 5-15 minutes.
  pause
  exit /b 1
)
echo Examples of a period: jan-mar   apr-jun   jan-sep   oct   nov-feb
set /p PERIOD=Period (months): 
set /p YEAR=Year [2026]: 
if "%YEAR%"=="" set YEAR=2026
set /p CUR=Currency USD or INR [INR]: 
if "%CUR%"=="" set CUR=INR
set /p EVENTS=Plant demo problems (spikes, step, idle VM, storage leak)? y/n [y]: 
set EV=demo
if /i "%EVENTS%"=="n" set EV=none
.venv\Scripts\python.exe -m scripts.generate_sample_bills --period %PERIOD% --year %YEAR% --currency %CUR% --events %EV%
if errorlevel 1 (
  echo.
  echo No bill was made - check the answers above and run make_bill.cmd again.
  pause
  exit /b 1
)
echo.
echo The bill is in output\bills\ - upload it on the dashboard or drag it onto run_demo.cmd.
pause
