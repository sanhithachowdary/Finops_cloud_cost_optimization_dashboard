@echo off
REM Validates the engine on the FinOps Foundation public FOCUS sample (data\samples\focus_sample_10000.csv).
REM Writes docs\evidence\public-sample\report.json; console log in output\public_sample_console.log
cd /d "%~dp0"
if not exist output mkdir output
.venv\Scripts\python.exe -m scripts.validate_public_sample data\samples\focus_sample_10000.csv --out docs\evidence\public-sample\report.json > output\public_sample_console.log 2>&1
echo EXIT CODE %ERRORLEVEL% >> output\public_sample_console.log
