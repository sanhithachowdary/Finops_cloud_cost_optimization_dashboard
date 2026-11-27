# Windows PowerShell: create a local virtual environment, install pinned dependencies,
# run lint/type checks, the full test suite (incl. NT-1..NT-5) and the KPI harness.
#
#   powershell -ExecutionPolicy Bypass -File scripts\run_checks.ps1      # (process-scoped bypass only)
#   .\scripts\run_checks.ps1 -SkipInstall                               # reuse an existing .venv
#
# Network access is needed once, for `pip install` from PyPI. Nothing is installed outside .venv.
# No cloud accounts are contacted: FINOP_MOCK_MODE=true and tests use local fixtures only.
param(
    [switch]$SkipInstall,
    [int]$Reps = 30
)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
$failed = @()

# Runs one step and records it as failed when its exit code is non-zero, so a failing step
# can never be hidden behind a final "EXIT CODE 0".
function Step([string]$Name, [scriptblock]$Command) {
    Write-Host "`n=== $Name ==="
    & $Command
    if ($LASTEXITCODE -ne 0) {
        Write-Host "*** FAILED: $Name (exit code $LASTEXITCODE)"
        $script:failed += $Name
    }
}

if (-not (Test-Path ".venv")) { py -3 -m venv .venv }
$py = Join-Path ".venv" "Scripts\python.exe"

if (-not $SkipInstall) {
    Step "pip upgrade" { & $py -m pip install --upgrade pip }
    Step "install dependencies" { & $py -m pip install -r requirements-dev.txt }
    if ($failed.Count -gt 0) {
        Write-Host "`nInstalling the libraries failed - check the internet connection and run again."
        exit 1
    }
}

$env:FINOP_MOCK_MODE = "true"
New-Item -ItemType Directory -Force -Path "output\evidence" | Out-Null

Step "ruff check" { & $py -m ruff check . }
Step "ruff format" { & $py -m ruff format --check . }
Step "mypy" { & $py -m mypy finops_engine }
Step "pytest" {
    & $py -m pytest tests -p no:cacheprovider --junitxml=output\evidence\pytest-junit-windows.xml 2>&1 |
        Tee-Object -FilePath output\evidence\pytest-output-windows.txt
}
Step "negative tests" { & $py -m pytest tests\negative -v -p no:cacheprovider }
Step "KPI harness" { & $py -m scripts.acceptance_harness --out-dir output\evidence --reps $Reps }

if ($failed.Count -gt 0) {
    Write-Host "`nFAILED STEPS: $($failed -join ', ')"
    exit 1
}
Write-Host "`nAll steps passed. Evidence written to output\evidence."
Write-Host "Start the dashboard with run_demo.cmd (sample data) or run_dashboard.cmd, then open"
Write-Host "  http://127.0.0.1:8765/dashboard"
exit 0
