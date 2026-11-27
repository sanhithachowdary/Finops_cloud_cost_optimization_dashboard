@echo off
REM Regenerates the sample bills with scripts\generate_sample_bills.py:
REM   data\demo\      the four instructor-approved bills in USD (reproduced exactly)
REM   data\demo-inr\  the same bills priced in Indian rupees
REM Extra options are passed through, e.g.  generate_bills.cmd --seed 7   (a new variation, written to output\bills)
cd /d "%~dp0"
.venv\Scripts\python.exe -c "import uvicorn, fastapi, pandas, sklearn" >nul 2>nul
if errorlevel 1 (
  echo Setup is not finished. Double-click run_checks.cmd, wait until its window says
  echo "Finished", then try again. It needs internet the first time and takes 5-15 minutes.
  pause
  exit /b 1
)
if "%~1"=="" (
  .venv\Scripts\python.exe -m scripts.generate_sample_bills
  .venv\Scripts\python.exe -m scripts.generate_sample_bills --currency INR
) else (
  .venv\Scripts\python.exe -m scripts.generate_sample_bills %*
)
pause
