# Code Structure

Each module has one job and talks to the others through plain Python objects
(`FocusRecord`, `UtilizationSample`, dicts). They can be developed and tested on their own and
are wired together by the API.

```
finops_engine/
├── schema/          FocusRecord: one billing line in FOCUS 1.0 form
├── engine_config.py versioned thresholds (config/*.json), validated at start-up
├── currency.py      reporting currency (₹): converts USD rows, formats money
├── connectors/      AWS / GCP / Azure SDK readers + mock data generator
├── ingestion/       upload path: validate, quarantine, de-duplicate, store
├── analytics/       spend totals, trends and breakdowns
├── ai/              anomaly detector and cost forecaster
├── evaluation/      precision/recall and forecast back-test against known answers
├── optimization/    right-sizing, idle, commitments, savings ledger
├── health.py        component status, degraded / safe mode
├── resilience.py    timeouts, retries, circuit breaker for cloud calls
├── telemetry.py     request latency and SLO numbers
├── exporter/        Prometheus metrics
├── api/             FastAPI app (app.py = original endpoints, routes_extended.py = new ones)
└── dashboard/       index.html: the 7-view web page
```

| Module | Input | Output | Main entry points |
|---|---|---|---|
| `ingestion` | CSV/JSON bill (bytes) | accepted `FocusRecord`s + batch report | `ingest_csv_file()`, `IngestionStore.ingest_rows()`, `validate_rows()` |
| `analytics` | records | totals, trends, breakdowns | `spend_overview()`, `spend_trend()`, `spend_breakdown()`, `period_comparison()` |
| `ai` | records | alerts / forecast | `AnomalyDetector.detect_anomalies()`, `CostForecaster.predict_future_cost()` |
| `evaluation` | records + ground truth | precision/recall, MAE/RMSE/MAPE | `evaluate_anomaly_detector()`, `backtest_forecast()` |
| `optimization` | records + utilisation samples | recommendations + savings summary | `generate_recommendations()` |
| `currency` | records / amounts | records in ₹, "₹12,34,567.89" | `to_base_currency()`, `format_money()` |
| `health`, `resilience`, `telemetry` | events from the other modules | status, safe mode, SLOs | `health_registry`, `call_with_resilience()` |
| `api` | HTTP requests | JSON | `finops_engine.api.app:app` |

**How a request flows:** `api/data_access.load_dataset()` picks the records (mock, live or
uploaded) → `analytics` / `ai` / `optimization` compute → the endpoint returns JSON → the
dashboard draws it.

## Tools (`scripts/`, run as `python -m scripts.<name>`)

| Script | Purpose |
|---|---|
| `generate_sample_bills` + `period_bills` | make sample bills (approved files, or any dates from `data/sample-estate.csv`) |
| `utilization_from_billing` | turn a bill's CPU column into utilisation samples; upload to the dashboard |
| `sample_data_report` | run everything on `data/demo/` and write the HTML/MD/JSON report |
| `multi_bill_check` | upload several bills and check totals, duplicates and anomalies |
| `acceptance_harness`, `benchmark_api` | KPI evidence and latency benchmark |
| `validate_public_sample` | check against the FinOps Foundation public sample |
| `generate_fixtures` | rebuild the versioned test fixtures |
| `anomaly_detection`, `cost_forecasting`, `rightsizing`, `export_recommendations` | small command-line front ends |
| `check_dashboard` | diagnose whether the local dashboard answers |

## Launchers (Windows, double-click)

`run_checks.cmd` (set up + test), `run_demo.cmd`, `run_dashboard.cmd`, `make_bill.cmd`,
`generate_bills.cmd`, `run_sample_report.cmd`, `load_sample.cmd`, `run_public_sample.cmd`,
`check_dashboard.cmd`.
