# Gap Analysis & Implementation Plan (Phase 1–2 baseline)

Recorded **before** any code change, against the supplied ZIP extract of
`priyaranjan-sahu/multi-cloud-finops` (package version `1.3.0`).

## 1. Baseline inventory

| Item | Finding |
|---|---|
| Project root | `multi-cloud-finops-main/` (80 files, ~4.2k lines) |
| Version control | **Not a Git repository** (ZIP extract). Local Git initialised with user approval; commit `Baseline: original …` on `main` holds the unmodified files. |
| Language / framework | Python ≥3.10, FastAPI, Pydantic v2 + pydantic-settings, pandas/NumPy/SciPy/scikit-learn |
| Entry points | `uvicorn finops_engine.api.app:app`, `src/main.py`, CLI `python -m scripts.<tool>` |
| Data model | `FocusRecord` (FOCUS-1.0-aligned) in `finops_engine/schema/focus_spec.py` |
| Data sources | Deterministic mock generator; AWS Cost Explorer, GCP BigQuery export, Azure Cost Management connectors (live SDK calls, untested against real accounts) |
| Ops | Prometheus exporter (`/metrics`), Grafana dashboard JSON, Docker/Compose, Terraform, K8s + KEDA |
| Tests | 10 files, 76 test cases (`pytest`) |
| CI | `.github/workflows/ci.yml` (ruff, ruff format, mypy, pytest + coverage) |

### Baseline check results (this workspace)

Environment: Linux, CPython 3.13.16, pandas 3.0.5, NumPy 2.5.3, scikit-learn 1.9.1,
SciPy 1.18.1, FastAPI 0.142.2 / pydantic-settings 2.15.0 / cachetools 7.2.0 /
prometheus-client 0.26.0 (official GitHub sources at the pinned tags, fetched with user
approval because PyPI is blocked by the session allow-list). Cloud SDKs (boto3,
google-cloud-bigquery, azure-*) are **not installed**.

| Check | Result |
|---|---|
| `ruff check .` | pass |
| `ruff format --check .` | pass (44 files) |
| `mypy finops_engine` | pass (18 files) |
| `pytest tests/` | **65 passed, 11 failed**: all 11 are connector tests that `patch()` cloud SDK modules that are not installed in this environment (environmental, not a code defect) |

> Note: the environment differs from the pinned versions (Python 3.13 vs 3.10 in CI; pandas 3 vs 2.3.3).
> Results are therefore *indicative* until re-run on the pinned stack (`scripts/run_checks.ps1` / CI).

## 2. Requirement → code gap matrix

Status legend: **I** = Implemented, **P** = Partial, **M** = Missing, **B** = Blocked.

| Req | Current implementation (evidence) | Status | Gap | Planned change | Test / evidence |
|---|---|---|---|---|---|
| FR-1 ingestion | Connectors pull AWS/GCP/Azure via SDKs (`connectors/*.py`); mock generator. Pydantic types only. | P | No file/export ingestion, no OpenCost/Kubecost, no schema-version/currency/unit/timestamp/duplicate validation, no quarantine, malformed rows silently dropped (logged only), no provenance. | `finops_engine/ingestion/` validator + quarantine + idempotent store + OpenCost allocation adapter + evidence files; `POST /api/v1/ingest/*` | `tests/test_ingestion.py`, NT-1, NT-2 |
| FR-1 data quality | Non-USD GCP rows skipped with a warning only | P | Totals can silently change; no record of rejected rows | Quarantine with reasons; reconciliation `input = accepted + quarantined + duplicates` | NT-1 |
| FR-2 spend analytics | `/costs/summary`: totals by provider/service/region | P | No daily/weekly/monthly trends, no account/resource/workload breakdown, no period comparison, no independent oracle | `finops_engine/analytics/spend.py`, `/costs/trends`, `/costs/breakdown` | `tests/test_spend_analytics.py` (pure-Python oracle) |
| FR-3 anomaly | IsolationForest + rolling z-score (`ai/anomaly_detector.py`) | P | Alert lacks id/status/entity/window/score evidence; no versioned config; no precision vs ground truth | Extend alert contract (backward-compatible), versioned config file, evaluation module with independent labelled fixture | `tests/test_evaluation.py`, KPI-3 |
| FR-3 forecast | Linear regression + 95 % PI (`ai/cost_forecaster.py`) | P | No held-out accuracy, no baseline comparison | Back-test (MAE/RMSE/MAPE) vs naive baselines | KPI-2 |
| FR-4 right-sizing | Endpoint returns **HTTP 402** "Enterprise" teaser; CLI prints banner and exits 1 | M | Whole feature absent | `optimization/rightsizing.py` with utilisation inputs, validation contract, telemetry | `tests/test_optimization.py`, NT-3 |
| FR-5 idle | Enterprise teaser only | M | Absent | `optimization/idle.py` with safeguards (protected tags, unknown telemetry) | NT-3, NT-5 |
| FR-6 commitments | Enterprise teaser only | M | Absent | `optimization/commitments.py` — refuses when history/stability insufficient | `tests/test_optimization.py` |
| Savings accounting | None | M | No estimated-vs-realised split, no overlap control | `optimization/savings.py` | KPI-1 |
| FR-7 dashboard | Grafana JSON (3 panels: spend, anomalies, distribution); Swagger | P | No coherent product views (spend trends, forecast, optimisation, savings, ops) | Built-in dashboard `/dashboard` (no external CDN) + extended Grafana panels | API/UI integration tests |
| FR-8 failure handling | Fail-closed 503 when no live data; partial-failure label | P | No timeouts/retries/breaker, no health/quality state, metrics refresh failures swallowed, no recovery evidence | `resilience.py`, `health.py`, `/api/v1/ops/*`, recovery event log | NT-4, NT-5, KPI-4 |
| NFR-1 performance | None | M | No latency/throughput measurement | Latency histogram + in-process p95 + benchmark harness | `scripts/acceptance_harness.py` |
| NFR-2 reliability | Some negative tests (fail-closed) | P | Five mandatory negative tests absent | `tests/negative/test_nt1..nt5` | negative-test-results.md |
| NFR-3 SLOs | None | M | No SLO definitions | SLO config + `/api/v1/ops/slo` | testing-and-acceptance.md |
| NFR-4 timeouts/retries/idempotency | TTL cache only | P | No bounded retry, duplicate ingestion not prevented | Bounded retry/timeouts, idempotent ingestion keyed by SHA-256 | NT-4, ingestion tests |
| NFR-5 engineering | Tests, CI, ruff, mypy, pinned deps | I/P | Not under VCS locally; config validation partial | Git initialised; config validation for new settings | CI + local run |
| NFR-6 observability | Prometheus metrics (cost, anomalies, request count) | P | No health indicators, quality metrics, degraded mode, operator guidance | New gauges, ops endpoints, operator guide | docs/setup-and-operations.md |
| KPI-1..6 / AC-1..4 | None | M/B | No harness; **no Q2 baseline, sandbox, workload traces or hardware envelope supplied** | Harness + baseline template; relative KPIs reported as "blocked by missing Q2 baseline" | docs/evidence/KPI-results.md |
| Docs D1–D7 | README, CONTRIBUTING, SECURITY, CHANGELOG | P | No architecture/traceability/API contract/testing/limitations docs, no final report | `docs/*`, `FINAL_IMPLEMENTATION_REPORT.md` | — |

## 3. Prioritised implementation plan

1. **Data integrity first** (FR-1, NT-1, NT-2): validator, quarantine, idempotent store, OpenCost adapter.
2. **Analytics** (FR-2): trends + breakdowns, verified against an independent pure-Python oracle.
3. **Anomaly/forecast evidence** (FR-3): alert contract, versioned config, precision and back-test modules.
4. **Optimisation** (FR-4..6) with safety statuses; savings accounting with overlap control.
5. **Resilience & telemetry** (FR-8, NFR-4/6): retries/timeouts/breaker, health registry, metrics.
6. **API + dashboard** (FR-7).
7. **Tests**: NT-1..NT-5, unit, integration; **KPI harness**; evidence.
8. **Docs** and final report; secret scan; commit on `feature/finops-gap-implementation`.

Decisions that keep the change minimal:
- Existing modules, response fields and tests are preserved; new alert/forecast fields are *additive*.
- No new runtime dependencies (uploads use the raw request body; the dashboard has no CDN assets).
- The Enterprise teaser endpoints for change-intelligence/deployments are untouched; only the
  right-sizing teaser is replaced, because FR-4 requires a working feature.
- Cloud connectors are **not** exercised against real accounts (no authorisation given).
