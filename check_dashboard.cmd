@echo off
REM Diagnostic: checks whether the local dashboard server answers; writes output\dashboard_check.txt
cd /d "%~dp0"
.venv\Scripts\python.exe scripts\check_dashboard.py > output\dashboard_check.txt 2>&1
netstat -ano | findstr ":8765 :8000" >> output\dashboard_check.txt

tasklist /FI "IMAGENAME eq python.exe" >> output\dashboard_check.txt
