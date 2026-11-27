# Setup and Operations Guide

## 1. Prerequisites

- Python 3.10, 3.11 or 3.12 (the versions CI tests against). The cloud workspace used for
  this work ran 3.13.
- Internet access to PyPI once, to install the pinned `requirements*.txt`.
- No cloud credentials are needed: the default is `FINOP_MOCK_MODE=true` with SYNTHETIC data.

## 2. Install and run on Windows (PowerShell)

```powershell
cd C:\Users\<you>\Downloads\multi-cloud-finops-main\multi-cloud-finops-main
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
Copy-Item .env.example .env          # optional; edit values, never put cloud keys in it
.\.venv\Scripts\python.exe -m uvicorn finops_engine.api.app:app --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000/dashboard>.

**Shortcut on Windows:** after one `run_checks.cmd` run (which creates `.venv`), double-click
`run_dashboard.cmd`. It serves the dashboard on **<http://127.0.0.1:8765/dashboard>**, because on
this PC port 8000 is already held by a Windows service (`svchost.exe`). Close the console
window to stop it. `check_dashboard.cmd` writes a health and port diagnostic to
`output\dashboard_check.txt`. Verified on 2026-10-16: all 7 views load, with no console
errors. Swagger docs are at `/docs` and Prometheus metrics
at `/metrics`.

To run every check in one go:
`powershell -ExecutionPolicy Bypass -File scripts\run_checks.ps1`. The `Bypass` flag
applies to that single process only. If you prefer, double-click `run_checks.cmd` in
the project folder instead; the console output is saved to `output\run_checks_console.log`.
Verified on 2026-10-16: exit code 0, 170 tests passed.

## 3. Install and run on Linux / macOS

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
make run                      # uvicorn on 0.0.0.0:8000 with --reload
make check                    # ruff + format check + mypy + pytest
make test-negative            # NT-1..NT-5 only
make acceptance               # KPI harness -> docs/evidence/kpi-results.json
```

Docker: `cp .env.example .env`, set a Grafana password, then `docker compose up --build`.
This path was not executed in this work because Docker was unavailable.

## 4. Configuration

| Variable | Default | Purpose |
|---|---|---|
| `FINOP_MOCK_MODE` | `true` | SYNTHETIC data. Set `false` for live connectors (credentials come from the SDK chain). |
| `FINOP_API_KEY` | empty | When set, `/api/*` and `/metrics` require `X-API-Key`. Required when not in mock mode. |
| `FINOP_CORS_ORIGINS` | empty | Allow-list. Required in production. |
| `FINOP_ENGINE_CONFIG` | `config/finops-engine.json` | Versioned thresholds. Validated at startup: unknown keys or out-of-range values stop the service. |
| `FINOP_EVIDENCE_DIR` | `output/evidence` | Ingestion reports, quarantined rows and `health-events.jsonl`. Git-ignored. Empty disables it. |
| `FINOP_METRICS_REFRESH_SECONDS` | `15` | Metrics loop interval. Telemetry counts as stale after 3× this. |

## Reporting currency (Indian rupees)

The shipped configuration reports every amount in **INR (₹)**. The bills themselves (sample
data, mock data, AWS/GCP/Azure connectors) are in USD and are converted with **one fixed rate**
set in the config: `ingestion.fx_rates_to_base = {"USD": 94.95}`. That was the market rate on
24 October 2026 (banklive.net mid-market 94.9455; Upstox 95.0025 on 23 October). There is no live
FX feed; to use another rate, edit the number in `config/finops-engine.json` and
`config/finops-engine.sample.json`, bump `"version"` and restart.

| Config file | Currency | Used by |
|---|---|---|
| `config/finops-engine.json` (`2026.10-v1-inr`) | INR | the service by default, `run_dashboard.cmd` |
| `config/finops-engine.sample.json` (`2026.10-v1-sample-inr`) | INR | `run_demo.cmd`, `run_sample_report.cmd` |
| `config/finops-engine.usd.json` (`2026.10-v1`) | USD | the tests and the KPI harness (their fixtures are in USD) |

Amount thresholds are in the reporting currency: anomaly floor ₹475/day (sample config ₹95,
both ≈ the earlier $5 / $1), severity HIGH above ₹19,000 and MEDIUM above ₹4,750 excess.
JSON field names keep their `*_usd` suffix for API compatibility; `/healthz` returns the
`currency` the amounts are in, and every anomaly alert carries `currency`.

To change a threshold, edit `config/finops-engine.json`, bump `"version"` and restart.
Every alert, recommendation and ingestion report records the config version that produced it.

## 5. Typical workflow (real data, offline)

1. Export billing in FOCUS format (or CSV with FOCUS column names) from your provider.
2. In the dashboard, go to **Operations & validation → Ingest** and upload the file. You can
   also use the API:
   `curl -X POST "http://127.0.0.1:8000/api/v1/ingest/billing?format=csv&source_name=jun.csv" -H "Content-Type: text/csv" --data-binary @jun.csv`
3. Check the batch report: reconciliation, quarantine reasons, attribution share.
4. Optionally upload utilisation samples (`/ingest/utilization`) or an OpenCost export
   (`/ingest/opencost?provider=AWS`).
5. Switch the dashboard **Source** to `ingested`.

## 6. Operator guide: health, degraded and safe mode

Check `GET /api/v1/ops/health`, the dashboard's Operations view, or Grafana.

| Signal | Meaning | Action |
|---|---|---|
| `mode = safe` | A batch exceeded the quarantine ratio (default 20%). All recommendations are `suppressed`. | Open `/api/v1/ingest/reports` and `output/evidence/ingestion/batch-<id>.json`, fix the export, re-ingest. Safe mode clears automatically when a clean batch is ingested. To start fresh, restart the process. |
| `connector:<X>` `failed` | Timeouts or errors after bounded retries. The breaker fails fast for `breaker_reset_seconds`. | Check credentials and network. Recovery is automatic and recorded as a `recovered` event. |
| A component is `unknown` | No signal, or a stale signal (older than 3× the interval). | Treat its outputs as unverified. Check the metrics loop and logs. |
| `utilization_telemetry` `degraded` | No utilisation data, so right-sizing and idle were **not evaluated**. | Ingest utilisation samples or an OpenCost export. |
| `/ops/slo` `insufficient_evidence` | Fewer than 20 requests sampled | Generate traffic or use Prometheus over a longer window. |

Logs are standard Python logging (`finops.*` loggers). Health transitions are logged at
WARNING level and appended to `health-events.jsonl`.

## 7. Troubleshooting

| Symptom | Fix |
|---|---|
| `RuntimeError: FATAL: FINOP_API_KEY must be set` | You set `FINOP_MOCK_MODE=false` or production mode without an API key. Set one, or `FINOP_ALLOW_ANONYMOUS=true` for local use only. |
| `ValidationError` at startup | The engine config file has a typo or an out-of-range value. The message names the field. |
| Dashboard shows "401" | Enter the API key in the header box. It is held in memory only. |
| `404 No ingested billing data` | Upload a billing export first, or switch Source to `mock`. |
| HTTP 413 on upload | The file is larger than `ingestion.max_upload_bytes` (20 MB). Split it or raise the limit. |
| Ingested data disappeared | The store is in-memory. Restarting the process (or running multiple workers) loses or splits it. Re-ingest. |

## 8. Rollback

All work is on Git branch `feature/finops-gap-implementation`. Branch `main` holds the
untouched original files.

- Return to the original state: `git checkout main`
- Undo everything: delete the feature branch with `git branch -D feature/finops-gap-implementation` after switching to `main`
- Revert one file: `git checkout main -- <path>`
