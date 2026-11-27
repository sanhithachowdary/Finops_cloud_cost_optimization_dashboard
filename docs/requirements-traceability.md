# Requirements Traceability Matrix

Status: **I** = Implemented and tested · **P** = Partial · **B** = Blocked. Paths are relative to the repository root.
Before-state for every row: `docs/gap-analysis.md`.

| Req | Requirement (summary) | Implementation | Tests | Evidence | Status |
|---|---|---|---|---|---|
| FR-1 | Ingest billing exports and/or OpenCost/Kubecost | `finops_engine/ingestion/{validator,store,opencost}.py`; `POST /api/v1/ingest/{billing,opencost,utilization}`; existing SDK connectors (`connectors/*`) | `tests/test_ingestion.py`, `tests/negative/test_nt1_*`, `test_nt2_*` | `docs/evidence/pytest-output.txt`; real-data check `docs/evidence/public-sample/` | **I** for files and exported OpenCost JSON (validated on anonymised real FOCUS data). **P** for live OpenCost/Kubecost API and live cloud connectors (not exercised) |
| FR-1 | Validate schema/version, provenance, currency/units, timestamps, required fields, duplicates, missing/malformed values, attribution. Quarantine; never corrupt totals | `validator.validate_rows`, `store._build_report` (reconciliation, SHA-256, config version) | NT-1, NT-2, `test_ingestion.py` | `negative-test-results.md` | **I** |
| FR-2 | Current/historical spend; daily/weekly/monthly; by provider, account, service, resource, k8s workload | `analytics/spend.py`; `/costs/overview`, `/costs/trends`, `/costs/breakdown` (+ existing `/costs/summary`) | `tests/test_spend_analytics.py` (hand-computed oracle and pure-Python recomputation) | `fixtures/billing_small_v1.expected.json`; harness `spend_oracle` = pass (14/14 checks) | **I** |
| FR-2 | Reproducible command/test protocol with baseline comparison | `make test`, `python -m scripts.acceptance_harness` | same | `kpi-results.json` | **I** |
| FR-3 | Independently testable anomaly detection + forecasting, versioned config, fixtures | `ai/anomaly_detector.py` (extended), `ai/cost_forecaster.py` (extended), `engine_config.py`, `config/finops-engine.json`, `scripts/generate_fixtures.py` | `tests/test_evaluation.py`, `tests/test_engine_config.py` | fixtures `*_v1` | **I** |
| FR-3 | Alerts carry severity/status, entity, observed vs expected, score/reason, window, evidence | Extended alert dict + `api/models.AnomalyItem` | `test_evaluation.py::test_alert_contract_fields` | dashboard Anomalies view screenshot | **I** |
| FR-3 | Precision vs independent oracle; forecast accuracy on held-out data | `evaluation/anomaly_eval.py`, `evaluation/forecast_eval.py`, `/forecast/backtest` | `test_evaluation.py` | `KPI-results.md` (KPI-2, KPI-3) | **I** (synthetic data only) |
| FR-4 | Right-sizing: inputs, outputs, config, rationale, validation/error contracts, telemetry | `optimization/rightsizing.py`, `UtilizationSample` contract, `/recommendations/rightsizing` (replaces 402 teaser), `scripts/rightsizing.py`, Prometheus `finops_recommendations` | `test_optimization.py`, NT-3 | `api-and-data-contracts.md` | **I** |
| FR-5 | Idle-resource detection with evidence and safeguards | `optimization/idle.py` | `test_optimization.py`, NT-3 | — | **I** |
| FR-6 | Commitment opportunities only where data supports them | `optimization/commitments.py` (history, CV, discount-rate and overlap checks) | `test_optimization.py::test_commitments_require_history_and_discount_rate` | — | **I** (discount rates are configured assumptions) |
| FR-4–6 | Each recommendation: resource, reason, assumptions, savings, confidence/limitations, safe next action; no auto-change | `optimization/models.Recommendation` (`auto_apply=False`) | `test_every_recommendation_has_required_contract`, NT-3 | — | **I** |
| FR-4–6 | Estimated vs realised savings; reference cost, period, formula, overlap controls | `optimization/engine.py` (`savings_summary`, `realized_savings`), `optimization/costs.py`; `/savings/summary` | `test_optimization.py` | `KPI-results.md` (KPI-1) | **I** for the estimated ledger. Realised is implemented but **B** for measurement (no post-change billing) |
| FR-7 | Coherent dashboard: overview, spend, anomalies, forecast, optimisation, savings, validation/ops | `finops_engine/dashboard/index.html` at `/dashboard`; Grafana panels extended/fixed | `test_api_extended.py::test_dashboard_*` | `docs/evidence/screenshots/*.png` | **I** |
| FR-8 | Detect failures, safe/degraded mode, health/quality state, recovery evidence | `health.py`, `resilience.py`, `/ops/health`, `/ops/quality`, `/healthz`, `health-events.jsonl` | NT-4, NT-5, NT-1 (safe mode) | `KPI-results.md` (KPI-4) | **I** |
| NFR-1 | Latency, throughput, resource usage incl. p95 | Histogram `finops_api_request_duration_seconds`, `/ops/slo`, `scripts/benchmark_api.py` (p50/p95/p99, rps, max RSS) | `test_api_extended.py::test_latency_metrics_and_slo_after_traffic` | `bench-final.json`, `bench-baseline.json`, `workload-trace-*.csv` | **I** (in-process measurement only) |
| NFR-2 | Explicit mandatory negative tests | `tests/negative/` | 38 tests | `negative-test-results.md` | **I** |
| NFR-3 | Measurable SLOs | `engine_config.SLOConfig`, `/api/v1/ops/slo` | NT-5 SLO test | `testing-and-acceptance.md` §SLOs | **I** (availability needs external probing) |
| NFR-4 | Timeouts, concurrency, retries, partial failures don't lose or duplicate work | `resilience.py`, single-flight cache (`condition=`), idempotent ingestion | NT-4, `test_concurrent_ingestion_of_same_file_creates_one_batch` | — | **I** |
| NFR-5 | Automated tests, clear interfaces, validated config, VCS, repeatable build/run | Config validated at startup; Git initialised; CI updated; `Makefile`, `scripts/run_checks.ps1` | `test_engine_config.py` | `static-checks.txt` | **I**. Verified on Windows with the exact pins (`docs/evidence/windows/`). CI not executed. |
| NFR-6 | Logs, health indicators, quality metrics, operator guidance, degraded mode, recovery evidence | Structured health events, `finops_component_status`, `finops_safe_mode`, `operator_guidance`, `setup-and-operations.md` | NT-4, NT-5 | screenshots `light-operations.png` | **I** |
| NT-1…NT-5 | Mandatory negative tests | `tests/negative/test_nt{1..5}_*.py` | 6 + 6 + 11 + 8 + 7 | `negative-test-results.md` | **I** (38/38 pass) |
| KPI-1 | ≥5% vs Q2 reference | Savings ledger + harness; `scripts/sample_data_report.py` | `tests/test_sample_report.py` | `KPI-results.md`, `sample-data/report.html` | **I** on instructor-approved sample data: 7.91% (met). 30.45% on synthetic fixture. Real bills: none exist |
| KPI-2 | Forecast accuracy | Back-test | `test_evaluation.py` | `KPI-results.md` | **I** (synthetic) |
| KPI-3 | Anomaly precision | Evaluation | `test_evaluation.py` | `KPI-results.md` | **I** (synthetic) |
| KPI-4 | Recovery rate | Fault campaign in harness | NT-4 | `kpi-results.json` | **I** (local simulated faults) |
| KPI-5 | Error rate ≤0.8× Q2 | Benchmark | — | `KPI-results.md` | **I** vs original code as reference (0 vs 0 errors, met) |
| KPI-6 | p95 ≤0.80× Q2 | Benchmark | — | `KPI-results.md` | **I** vs original code as reference (ratio 0.043, met) |
| AC-1 | Sandbox validation and realised savings | `scripts/sample_data_report.py`, `scripts/validate_public_sample.py` | `tests/test_sample_report.py` | `sample-data/`, `public-sample/` | **P**: met on instructor-approved sample data and anonymised public data; sandbox and realised savings need a live account |
| AC-2 | Boundary/failure, billing complexity, attribution, safe recommendations | NT-1…NT-5 | — | — | **I** (fixtures) |
| AC-3 | Independent acceptance evidence, fault campaign, versioned config, traces | Harness, traces | — | — | **P** (not independent, synthetic) |
| AC-4 | Same versions/workload/hardware for comparisons | Harness runs both in one session | — | `bench-*.json` | **P** (pre-change proxy only) |

## Source-to-claim map (D1)

| Claim in the report | Source |
|---|---|
| "All 5 negative tests pass" | `docs/evidence/negative-tests-output.txt` |
| "159 passed, 3 skipped" | `docs/evidence/pytest-output.txt`, `pytest-junit.xml` |
| "lint/format/type clean" | `docs/evidence/static-checks.txt` |
| KPI values | `docs/evidence/kpi-results.json` (generated) → `KPI-results.md` |
| Spend correctness | `tests/fixtures/billing_small_v1.expected.json` (hand-computed) + `test_spend_analytics.py` |
| Dashboard renders all views | `docs/evidence/screenshots/` (headless Chromium, SYNTHETIC mock data) |
| OpenCost field names | OpenCost source `core/pkg/opencost/allocation.go`, `allocation_json.go`, `allocationprops.go` |

## Re-audit against the requirements PDF (2026-10-24)

Every row above was re-checked after the rupee, own-date-bill and multiple-bill changes
(`pytest`: 207 passed, 3 skipped in the cloud workspace; **218 passed, 0 skipped on the user's Windows PC, run 6**; `scripts/multi_bill_check.py` 6/6).

| Area | Status | Change / evidence in this audit |
|---|---|---|
| FR-1 ingestion and quality | **I** (files, exported OpenCost); live APIs **P** | Gap found and fixed: a line restated with a different amount in a *later* bill was added again. Now quarantined (`conflicting_duplicate_across_batches`); same-amount repeats across bills, including USD vs INR exports, are counted once. `tests/test_multi_bill.py` |
| FR-1 currency | **I** | Reporting currency INR at a configured rate; USD rows converted on ingestion and from connectors. `tests/test_currency.py` |
| FR-2 spend analytics | **I** | Multiple bills (Jan–Sep, 3 uploads) match an independent oracle in total and per provider; account, Kubernetes-workload and unattributed breakdowns shown. `docs/evidence/multi-bill/` |
| FR-3 anomaly and forecast | **I** (synthetic and generated data) | Config v2 (14-day baseline, z ≥ 3.0): dev P 1.00 / R 1.00, independent hold-out P 1.00 / R 0.778; 3/3 planted events found across multiple bills (P 0.75). The threshold was chosen after looking at dev and generated data; the hold-out result is the unbiased one and did not change |
| FR-4/5/6 recommendations | **I** | Gap fixed: idle-review plus right-sizing on the same resource double-counted "potential" savings. `tests/test_optimization.py` |
| FR-7 dashboard | **I** | ₹ display, 180/365-day ranges for longer histories, deep link `?source=ingested&days=365` |
| FR-8, NFR-1…6, NT-1…5 | **I** as before | NT suite unchanged and passing |
| KPI-1/5/6 vs Q2 | **Met on instructor-approved sample data**; real Q2 still not available | `KPI-results.md` (sample-data section) |
| AC-1 | **P** | Sample and anonymised public data validated; sandbox and realised savings need a live account |
| AC-3 | **P** | Evidence still produced by the implementer, not an independent party |
| Operating rule: dependency/CVE check | **Not run** | Needs PyPI/OSV access; `pip-audit` was not run (see `security-and-privacy.md`). Waiting for approval to run it on the user's PC |
| Operating rule: clean setup from instructions | **I** on the user's PC; second machine not yet | The ZIP was extracted into a new folder and set up from scratch with `run_checks.cmd` (new `.venv`, libraries downloaded). It exposed two problems, now fixed: ruff linted `.venv`, and the check script hid that failure. Rerun: all steps pass (`windows/fresh-install-zip-copy-run.txt`) |
| Docker image build, CI run | **Not run** | Docker Desktop not installed; CI not triggered (nothing pushed) |
