# FINAL IMPLEMENTATION REPORT
## FinOps Cloud-Cost Optimisation Dashboard (upgrade of `priyaranjan-sahu/multi-cloud-finops`)

| | |
|---|---|
| Repository worked on | `C:\Users\saira\Downloads\multi-cloud-finops-main\multi-cloud-finops-main` (ZIP extract of v1.3.0, Community Edition) |
| Version control | Git initialised locally with the user's approval. `main` = unmodified baseline commit; `feature/finops-gap-implementation` = this work. Nothing pushed. |
| Date | 2026-10-12 |
| Test environment | Linux cloud workspace, 2 vCPU, CPython 3.13.16 (see §6 for the version caveat) |
| Data | SYNTHETIC only. No cloud account was contacted and no billable resource was created. |

---

## 1. Executive summary

The existing open-core FastAPI project already had cost connectors, a FOCUS schema, an
anomaly detector, a linear forecaster and Prometheus/Grafana. It was improved in place into
an integrated, tested FinOps dashboard. **No rewrite was done.** All original modules,
endpoints and 76 tests were kept. Changes to existing files are additive, with two
deliberate behaviour changes listed in §4.

**Added:**
- validated, idempotent billing and OpenCost ingestion with quarantine and reconciliation
- spend trends and breakdowns
- evidence-bearing anomaly alerts, with precision measured against independent ground truth
- forecast back-testing
- right-sizing, idle and commitment recommendations with a savings ledger
- resilience (timeouts, retries, breaker, saturation guard)
- a health, degraded and safe mode
- SLOs, a 7-view built-in dashboard, and the 5 mandatory negative tests
- a KPI harness and the documentation set

**Results (measured on synthetic fixtures):**
- **Tests:** 159 passed, 3 skipped; NT-1 to NT-5 38/38 passed; lint, format and types clean.
- **Anomalies:** precision 0.91 (dev) and 1.00 (independent hold-out), against 0.56 and 0.50
  for the pre-change rule.
- **Forecast:** MAPE 3.0–3.9%, beating naive baselines.
- **Recovery:** 20/20 after fault injection.
- **Estimated savings:** 30.45% on the benchmark fixture. This is simulated, not realised.

**Update 2026-10-16:**
- **Pinned stack:** verified on the user's Windows PC. Final run (run 6, 2026-10-24, rupees, own-date bills, multi-bill checks, config v2): 218 passed, 0 skipped;
  38 negative-test cases passed; lint, format and mypy clean.
- **Real-world data:** validated on anonymised FOCUS 1.0 data from the FinOps Foundation. Spend
  matches an independent oracle exactly. The run exposed 5 real-data bugs, all fixed with
  regression tests (`docs/evidence/public-sample/README.md`).
- **Dashboard:** verified live, all 7 views, on port 8765 via `run_dashboard.cmd`.
- **User-generated sample bills (4 files):** all accepted, totals exact. Testing them found
  that a step change off a flat baseline was never alerted; fixed with regression tests.
  Added a converter for a per-row utilisation column
  (`docs/evidence/user-samples/README.md`). These files are generated, not a Q2 baseline.

- **Instructor-approved sample data:** no real bills exist for this college project, and the
  instructor approved generated sample data as the reference. On it: KPI-1 7.91% (met, target
  ≥5%), KPI-5 0 errors (met), KPI-6 p95 ratio 0.043 vs the original code (met); KPI-2/3/4
  reported. One click: `run_sample_report.cmd` (report) and `run_demo.cmd` (dashboard).
  Evidence: `docs/evidence/sample-data/`, `docs/evidence/KPI-results.md`.

- **Rupees (2026-10-24):** all amounts are reported in INR, converted from the USD bills at a
  fixed configured rate of ₹94.95 per $1 (`docs/setup-and-operations.md`).

- **Own-date bills and multiple uploads (2026-10-24):** `make_bill.cmd` / `generate_sample_bills.py
  --period jan-mar` generate bills for any months from one stable estate; several bills (USD and
  INR, overlapping) upload together with totals equal to an independent oracle and all planted
  anomalies found (`docs/evidence/multi-bill/`). Two bugs fixed (restated lines across bills;
  double-counted potential savings). Anomaly config v2. Re-audit: `docs/requirements-traceability.md`.

- **Code clean-up (2026-10-24):** dead and duplicated code removed without changing results
  (generated bills, sample report, multi-bill check and API responses compared before and after:
  identical except the removed teaser list). Module guide: `docs/code-structure.md`.

**Still blocked with real data only:** KPI-1, KPI-5 and KPI-6 against a real Q2 bill, plus AC-1
sandbox validation and realised savings. No Q2 baseline, sandbox, workload traces or hardware envelope was
supplied. A collection template is provided.

## 2. Architecture overview

See `docs/architecture.md` (component diagram, data flow, design trade-offs).
In short:

1. **Sources** (exports, OpenCost JSON, SDK connectors, mock) feed into the validator.
2. **The validator** quarantines bad rows; accepted rows go to the idempotent store.
3. **A dataset resolver** (`source=mock|live|ingested`) feeds analytics, the anomaly
   detector, the forecaster and evaluation, and the optimisation engine.
4. **These modules** are served through FastAPI, `/dashboard` and Prometheus.
5. **Cross-cutting:** versioned config, the health registry with safe mode, and the
   resilience wrapper.

## 3. What already existed and was reused

| Existing asset | Reuse |
|---|---|
| `FocusRecord` schema, `categorize_service` | Reused; added an optional `tags` field (FOCUS "Tags") for workload attribution |
| AWS / GCP / Azure connectors | Reused unchanged; now called through the resilience wrapper |
| `MockTelemetryConnector` | Reused; added an optional fixed `end_date` and synthetic utilisation profiles. Output is identical by default. |
| `AnomalyDetector` (IsolationForest + z-score) | Reused and extended (alert contract, config, materiality gate, cache) |
| `CostForecaster` (linear + t-interval) | Reused; added status/method metadata and `predict_from_daily_totals` for back-testing |
| FastAPI app, API-key auth, CORS policy, fail-closed 503, streaming export | Reused; new routes mounted under the same auth router |
| Prometheus exporter, Grafana provisioning, Docker/Compose/K8s/Terraform | Reused; metrics and panels added, two broken panels fixed |
| pytest suite (76 tests), ruff/mypy config, CI workflow | Reused; CI gained negative-test and harness steps |

## 4. What was missing and implemented (exact paths)

| Gap (from `docs/gap-analysis.md`) | Implementation |
|---|---|
| FR-1 ingestion and data quality | `finops_engine/ingestion/validator.py`, `store.py`, `opencost.py`, `__init__.py` |
| FR-2 spend analytics | `finops_engine/analytics/spend.py`, `__init__.py` |
| FR-3 evaluation and versioned config | `finops_engine/evaluation/{anomaly_eval,forecast_eval,__init__}.py`, `finops_engine/engine_config.py`, `config/finops-engine.json` |
| FR-3 alert contract | `finops_engine/ai/anomaly_detector.py` (modified), `finops_engine/api/models.py` (modified) |
| FR-4/5/6 optimisation and savings | `finops_engine/optimization/{models,costs,rightsizing,idle,commitments,engine,__init__}.py` |
| FR-7 dashboard | `finops_engine/dashboard/index.html`; `monitoring/grafana_dashboard.json` (modified) |
| FR-8 / NFR-4 / NFR-6 reliability and telemetry | `finops_engine/health.py`, `resilience.py`, `telemetry.py`; `connectors/__init__.py`, `exporter/*` (modified) |
| API surface | `finops_engine/api/routes_extended.py`, `api/data_access.py`; `api/app.py` (modified: middleware, error handler, `/healthz`, `/dashboard`, `source` parameter, config-driven detector) |
| CLI | `scripts/rightsizing.py`, `scripts/export_recommendations.py` (replaced banners with working tools), `scripts/generate_fixtures.py`, `scripts/benchmark_api.py`, `scripts/acceptance_harness.py`, `scripts/run_checks.ps1` |
| Tests | `tests/conftest.py`, `tests/negative/test_nt{1..5}_*.py`, `tests/test_{ingestion,spend_analytics,optimization,evaluation,engine_config,api_extended}.py`, `tests/fixtures/**` |
| Docs and evidence | `docs/*.md`, `docs/evidence/**`, this report; `README.md`, `CHANGELOG.md`, `.env.example`, `.gitignore`, `Makefile`, `pyproject.toml`, `.github/workflows/ci.yml` (modified) |

**Intentional behaviour changes:**
1. `GET /api/v1/recommendations/rightsizing` returns real recommendations instead of
   HTTP 402. FR-4 requires a working feature.
2. The 3 SDK-patching connector test modules now **skip** when the SDK is absent instead of
   erroring.

**Removed in the 2026-10-24 clean-up:** the change-intelligence and deployment-event
Enterprise teaser endpoints (they always answered HTTP 402), the advert-only scripts
`scripts/spot_optimization.py` and `scripts/change_intelligence.py`, and the
`enterprise_features_locked` list on `GET /`. **Not changed:** dependency pins.

## 5. Installation, configuration, run, test, troubleshooting

Full guide: `docs/setup-and-operations.md`. Quick commands for **Windows PowerShell**:

```powershell
cd C:\Users\saira\Downloads\multi-cloud-finops-main\multi-cloud-finops-main
git checkout feature/finops-gap-implementation
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest tests                 # full suite
.\.venv\Scripts\python.exe -m pytest tests\negative -v     # NT-1..NT-5
.\.venv\Scripts\python.exe -m scripts.acceptance_harness --out-dir output\evidence
.\.venv\Scripts\python.exe -m uvicorn finops_engine.api.app:app --host 127.0.0.1 --port 8000
# open http://127.0.0.1:8000/dashboard  (Source: mock), API docs at /docs
```

Or run everything in one go: `powershell -ExecutionPolicy Bypass -File scripts\run_checks.ps1`.
The bypass is process-scoped. You can also double-click `run_checks.cmd`. Verified on your PC on 2026-10-16: exit code 0.

Linux/macOS: `make install-dev && make check && make test-negative && make acceptance && make run`.

To try real data offline, upload a FOCUS CSV in **Operations & validation → Ingest**, then
switch **Source** to `ingested`.

## 6. Test matrix (exact commands, actual outcomes)

| Command | Outcome |
|---|---|
| `pytest tests/` on the **unmodified** baseline | 65 passed, 11 failed (SDK-patching connector tests; SDKs not installed) |
| `pytest tests -p no:cacheprovider --junitxml=docs/evidence/pytest-junit.xml` (final) | **159 passed, 3 skipped, 0 failed** (49 s) |
| `pytest tests/negative -v` | **38 passed** |
| negative + ingestion tests ×5 (flakiness) | 5/5 green |
| `ruff check .` / `ruff format --check .` / `mypy finops_engine` | pass / pass (94 files) / pass (40 files) |
| `python -m scripts.acceptance_harness --baseline-code-dir <baseline> --reps 30` | completed; spend oracle 14/14 checks pass |
| **Windows PC, pinned stack** (`run_checks.cmd`, run 1) | ruff/format pass. mypy 2.4.0: **30 typing errors**. 170 passed, 0 skipped. 38 negative passed. |
| **Windows PC, after the real-data fixes** (run 3) | ruff/format/mypy clean. **176 passed, 0 skipped**. **38 negative passed**. Exit code 0. |
| **Windows PC, after the flat-baseline fix and utilisation converter** (run 4) | ruff/format/mypy clean (101 files). **192 passed, 0 skipped**. **38 negative passed**. Exit code 0. |
| **Windows PC, with the sample-data report and unit fix** (run 5) | ruff/format/mypy clean (104 files). **195 passed, 0 skipped**. **38 negative passed**. Exit code 0. |
| **Windows PC, rupees + own-date bills + multi-bill checks + config v2** (run 6, 2026-10-24) | ruff/format/mypy clean (114 files). **218 passed, 0 skipped**. **38 negative passed**. KPI harness: dev P 1.00 / R 1.00, hold-out P 1.00 / R 0.778. Exit code 0. |
| **Public FOCUS sample** (`run_public_sample.cmd`) | 9,927/10,000 accepted. Spend = oracle (pass). Exact reconciliation. 5 bugs found and fixed. |
| **Windows PC, after the typing fix** (run 2) | ruff/format pass. **mypy: no issues**. **170 passed, 0 skipped**. **38 negative passed**. Harness OK. Exit code 0. Evidence: `docs/evidence/windows/` |
| Dashboard rendered in headless Chromium, 7 views, light and dark | no console errors; screenshots in `docs/evidence/screenshots/` |

**Version caveat:** tests ran on Python 3.13.16 with pandas 3.0.5, NumPy 2.5.3,
scikit-learn 1.9.1 and SciPy 1.18.1, which are newer than the pins. FastAPI 0.142.2,
pydantic 2.13.5, pydantic-settings 2.15.0, cachetools 7.2.0 and prometheus-client 0.26.0
are exactly the pinned versions, built from the official GitHub tags. You approved
fetching them that way because PyPI was blocked by the session allow-list.

**Resolved on 2026-10-16:**
- The pinned package versions, the PowerShell script and the cloud-SDK connector tests were
  all run on the user's Windows PC (Python 3.13.7).
- KPI-1 to KPI-5 reproduced identically there.

**Still not executed:**
- Python 3.10, 3.11 or 3.12 test runs
- a coverage report
- the GitHub Actions run
- the Docker build. Docker Desktop is not installed on the user's PC. The Dockerfile was changed to 1 uvicorn worker (2026-10-16) but has not been built.

## 7. KPI table

Details and value classes: `docs/evidence/KPI-results.md`. Raw data: `docs/evidence/kpi-results.json`.

| KPI | Measured | Baseline | Formula | Target | Evidence | Status |
|---|---|---|---|---|---|---|
| KPI-1 | 30.45% ($31,413.84 of $103,156.14/month) | Q2 not supplied | Σ actionable est. savings ÷ trailing-30-day cost ×30 | ≥5% vs Q2 | kpi-results.json `savings` | Simulated; **vs-Q2 blocked** |
| KPI-2 | MAPE 3.02% / MAE $101.84 (dev); 3.88% / $133.85 (hold-out) | Naive 7-day MAE $106.29 / $152.79 | MAE, RMSE, MAPE on 14 held-out days | Report vs approved data | `anomaly_forecast.*.forecast_backtest` | Measured (synthetic) |
| KPI-3 | P 1.00 / R 1.00 (dev); P 1.00 / R 0.778 (hold-out) with config v2 (v1: dev P 0.909) | Pre-change rule P 0.556 / 0.500 | TP ÷ (TP+FP) vs injected labels | From fixtures + oracle | `anomaly_forecast.*.anomaly_*` | Measured (synthetic) |
| KPI-4 | 20/20 = 1.00, max 0.40 s | No recovery tracking before | recovered ÷ injected | Measure after fault injection | `fault_campaign` | Measured (local) |
| KPI-5 | 0 / 135 = 0.0 | Pre-change 0 / 135 | 5xx ÷ requests | ≤0.8× Q2 | `bench-*.json` | Measured; **ratio vs Q2 blocked** (0/0) |
| KPI-6 | p95 64.65 ms | Pre-change 1,488.63 ms, same host (×0.043) | Nearest-rank p95, in-process | ≤0.80× Q2 | `bench-*.json` | Proxy only; **vs-Q2 blocked**. The gain comes entirely from caching repeated anomaly requests. |

## 8. Negative-test and recovery matrix

| NT | Result | What it proves |
|---|---|---|
| NT-1 billing complexity | 6/6 ✅ | 12 bad rows quarantined with reasons, duplicate counted once, cost reconciles, unit/FX/timestamp handling recorded, safe mode suppresses recommendations, API returns 422/413 with no stack traces |
| NT-2 attribution | 6/6 ✅ | Totals preserved; explicit unattributed bucket; conflicts flagged within and across batches; affected recommendations need review; OpenCost idle cost unattributed; provider-less rows quarantined |
| NT-3 unsafe recommendation | 11/11 ✅ | No actionable claim without evidence; rejected, insufficient or review statuses with reasons; nothing auto-applied |
| NT-4 latency/overload | 8/8 ✅ | Bounded time and attempts; breaker opens, fails fast and recovers (event with duration); saturation rejected in under 50 ms; partial failure labelled; API 503 in under 1.5 s |
| NT-5 insufficient telemetry | 7/7 ✅ | Failed, stale or missing signals reported as failed, unknown or insufficient; live mode without utilisation says "NOT evaluated"; 500s carry only an incident id |

Recovery evidence: `recovered` health events (`/api/v1/ops/health`, `health-events.jsonl`),
the KPI-4 fault campaign, and NT-4.

## 9. Security, data assumptions, limitations, operational risks

**Security** (`docs/security-and-privacy.md`):
- auth covers every new route
- the dashboard has a strict CSP and keeps the API key in memory only
- upload size and row limits; strict models
- no stack traces leak to clients
- bounded pools and buffers
- billing evidence lives in a git-ignored directory

**Secret scan of the final tree:** searched for AWS, GCP, Slack and GitHub key patterns,
private keys, Azure connection strings and `password|secret|token|api_key = "..."`
assignments. **No matches.** No `.env`, key, credential or tfvars files were added.

**Limitations** (`docs/limitations-and-assumptions.md`):
- synthetic data only; the Q2-relative KPIs are blocked
- in-process, single-run latency
- in-memory per-process store (a problem with `--workers 4`)
- live connectors and OpenCost API not exercised
- assumed discount rates
- linear forecast with no seasonality
- the 20% materiality gate was tuned on the dev set (the hold-out was evaluated once)

**Operational risks:**
- Quarantine evidence contains raw billing rows. Protect `output/evidence`.
- A hung SDK call holds a worker until it returns.
- A single shared API key has no rate limiting.

## 10. Remaining partial / blocked items

| Item | Why | Unblock by |
|---|---|---|
| KPI-1/5/6 vs Q2, AC-1 (sandbox and realised savings) | No Q2 baseline, sandbox, traces or hardware envelope. AC-1 is now partly covered by the public real-data sample. | Fill in `docs/evidence/q2-baseline-template.json` and provide a read-only sandbox, then rerun the harness on that hardware |
| Realised savings | No implemented change and no post-change billing | Implement a recommendation, ingest ≥14 days before and after, call `realized_savings()` |
| Live cloud / OpenCost integration | Not authorised; no endpoints | Your explicit approval plus read-only credentials for a sandbox account |
| Multi-worker consistency | In-memory store | Run 1 worker for ingestion, or add persistent storage (out of scope) |
| ~~Pinned-stack and Windows verification~~ | **Done 2026-10-16:** 170 passed, mypy clean | — |
| AC-3 independence | Evidence produced by the implementer | Have a second person rerun the harness and review |

## 11. Official references consulted

- OpenCost API docs (query parameters): <https://opencost.io/docs/integrations/api>
- OpenCost allocation JSON field names, read from source:
  <https://github.com/opencost/opencost/blob/develop/core/pkg/opencost/allocation.go>,
  `allocation_json.go`, `allocationprops.go`
- FOCUS specification: <https://focus.finops.org/>. **Not fetched during this work.** The
  column names (BilledCost, EffectiveCost, ChargePeriodStart, SubAccountId, Tags …) come
  from prior knowledge and from the project's existing `focus_spec.py`. Verify them against
  the FOCUS version you export.
- Dependency sources at the pinned tags: <https://github.com/fastapi/fastapi> (0.142.2),
  <https://github.com/pydantic/pydantic-settings> (v2.15.0),
  <https://github.com/tkem/cachetools> (v7.2.0),
  <https://github.com/prometheus/client_python> (v0.26.0)

## 12. File-change summary and rollback

The diff from `main` to `feature/finops-gap-implementation` covers 104 files: about
12.4k lines added (about 3.1k of them are fixture CSV rows) and 133 deleted.

Modified source files (lines +/−):

| File | +/− |
|---|---|
| `ai/anomaly_detector.py` | +169/−15 |
| `ai/cost_forecaster.py` | +31/−11 |
| `api/app.py` | +100/−29 |
| `api/models.py` | +20 |
| `connectors/__init__.py` | +21/−3 |
| `connectors/mock_connector.py` | +90/−2 |
| `exporter/metrics_exporter.py` | +80/−5 |
| `schema/focus_spec.py` | +5 |

**Rollback:**
- Everything: `git checkout main`
- One file: `git checkout main -- <path>`
- Discard the work entirely (after `git checkout main`):
  `git branch -D feature/finops-gap-implementation`
- To remove version control completely, delete the `.git` folder. The original files are in
  the `main` commit.

## 13. D7: case-study outline, demo checklist, viva notes

**Portfolio / report outline:**
1. Problem and why trust matters in FinOps.
2. Baseline system and gap analysis.
3. Data-integrity design: quarantine, reconciliation, idempotency.
4. Analytics and UTC semantics.
5. Detection and forecasting: method, materiality gate, dev vs hold-out evaluation.
6. Recommendation safety model and savings ledger (estimated vs realised, overlap).
7. Reliability engineering: breaker, saturation, health and safe mode, SLOs.
8. Evaluation: NT-1 to NT-5, KPI harness, threats to validity.
9. Limitations and future work: persistence, live adapters, seasonality, Q2 validation.

**Demo / video checklist:**
1. Run `pytest tests/negative -v` and show 38 passing.
2. Start the app and open `/dashboard` with Source = mock. Walk through Overview → Spend
   (weekly, by account) → Anomalies (open evidence) → Forecast (back-test table).
3. Go to Operations → Ingest and upload `tests/fixtures/negative/nt1_problematic_billing.csv`.
   Show the quarantine reasons, safe mode, and the Optimisation view showing all
   recommendations `suppressed`.
4. Restart, then ingest `billing_benchmark_v1.csv` and `utilization_benchmark_v1.json` with
   Source = ingested. Show Optimisation (statuses and reasons) and Savings (estimated vs
   potential vs realised = not measured).
5. Show `/api/v1/ops/health` and `/metrics`.
6. Run `python -m scripts.acceptance_harness` and open `KPI-results.md`, explaining why some
   rows are blocked.

**Viva notes:**
- **Expected questions:**
  - Why is the hold-out set needed? The gate was tuned on dev.
  - Why P10 for commitments? It is robust to spikes and conservative.
  - Why are requires-review savings excluded from the estimate? Avoids over-claiming.
  - Why is CPU alone not enough to call something idle? Absence of evidence.
  - What does safe mode protect against? Acting on corrupt data.
  - How are totals guaranteed not to be silently corrupted? Reconciliation identity.
  - What would change with real data? Thresholds, discount rates, the forecast model.
