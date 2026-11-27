@echo off
REM Launcher: runs scripts\run_checks.ps1 (creates .venv, installs pinned deps, runs checks/tests/KPI harness).
REM Console output is saved to output\run_checks_console.log
cd /d "%~dp0"
if not exist output mkdir output
echo Setting up and checking the project. The first run downloads the libraries and takes
echo 5-15 minutes. Do NOT start the dashboard until this window says "Finished".
echo Progress is written to output\run_checks_console.log
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run_checks.ps1 > output\run_checks_console.log 2>&1
set RC=%ERRORLEVEL%
echo EXIT CODE %RC% >> output\run_checks_console.log
echo.
if "%RC%"=="0" (
  echo Finished: all steps passed. You can now use run_demo.cmd, make_bill.cmd and the other launchers.
) else (
  echo Finished with problems ^(exit code %RC%^). Last lines of output\run_checks_console.log:
  powershell -NoProfile -Command "Get-Content output\run_checks_console.log -Tail 15"
)
pause
