# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added — FinOps Cost-Optimisation Dashboard (branch `feature/finops-gap-implementation`)
- Billing ingestion (`finops_engine/ingestion`): FOCUS CSV/JSON + exported OpenCost
  `/allocation` JSON; schema-version, currency/FX, unit, timestamp, duplicate, conflict,
  magnitude and attribution validation; quarantine with reasons; cost reconciliation;
  SHA-256 idempotency; evidence files; `POST /api/v1/ingest/{billing,opencost,utilization}`.
- Spend analytics (`finops_engine/analytics`): UTC daily/ISO-weekly/monthly trends,
  breakdowns by provider/account/service/region/resource/Kubernetes workload, explicit
  unattributed bucket, period comparison; `/costs/{overview,trends,breakdown}`.
- Anomaly alerts extended (additive): `alert_id`, status, entity, window, observed vs
  expected, score, reason, evidence, config version; configurable materiality gate.
- Forecast metadata (`status`, `method`, …) and back-testing vs naive baselines
  (`/forecast/backtest`, `finops_engine/evaluation`).
- Right-sizing, idle-resource and commitment recommendations with safety statuses and a
  savings ledger separating estimated / potential / realised savings
  (`finops_engine/optimization`, `/recommendations`, `/savings/summary`).
- Versioned, validated engine configuration (`config/finops-engine.json`).
- Resilience: per-dependency timeouts, bounded retries, circuit breaker, saturation guard,
  single-flight cost cache; health registry with stale detection, degraded/safe mode and
  recovery events; `/healthz`, `/api/v1/ops/{health,slo,quality}`; latency/error metrics.
- Built-in dashboard at `/dashboard` (7 views, no external assets); Grafana panels for
  latency, 5xx ratio, component health, safe mode, staleness, ingestion.
- Tests: NT-1…NT-5 (`tests/negative`), ingestion, analytics oracle, optimisation,
  evaluation, config, end-to-end API; versioned SYNTHETIC fixtures and generator;
  KPI harness (`scripts/acceptance_harness.py`, `scripts/benchmark_api.py`);
  `scripts/run_checks.ps1`; docs set under `docs/` and `FINAL_IMPLEMENTATION_REPORT.md`.

### Verified (2026-10-16)
- Ran on the user's Windows 11 PC with the exact pinned dependencies (`run_checks.cmd` →
  `scripts/run_checks.ps1`). 170 tests passed with 0 skipped, NT-1…NT-5 passed (38), ruff,
  format and mypy 2.4.0 are clean. Evidence is in `docs/evidence/windows/`.

### Changed — amounts reported in Indian rupees (2026-10-24)
- Shipped configs now report in **INR**: `ingestion.base_currency = "INR"`,
  `fx_rates_to_base = {"USD": 94.95}` (market rate, 24 Oct 2026). Anomaly floor and severity
  thresholds rescaled to rupees. `config/finops-engine.usd.json` keeps USD for tests and the KPI harness.
- `finops_engine/currency.py`: mock and live connector records (USD) are converted to the
  reporting currency; `₹` formatting with Indian digit grouping (₹12,34,567.89).
- Dashboard, sample report, anomaly reasons and commitment/right-sizing reasons use the
  reporting currency; `/healthz` and anomaly alerts now include `currency`.

### Added (2026-10-24)
- `scripts/generate_sample_bills.py` + `generate_bills.cmd`: the team's four bill-generator scripts
  merged into one project tool. Reproduces `data/demo/` exactly (tested); `--currency INR` writes
  rupee-priced bills (`data/demo-inr/`); `--seed N` makes new variations with the same planted events.

### Added — bills for any dates, multiple uploads (2026-10-24)
- `scripts/period_bills.py`, `generate_sample_bills.py --period jan-mar | --from/--to`, `make_bill.cmd`:
  long bills for any months from one stable estate (accounts, regions, team and Kubernetes tags,
  line `Id`s, CPU column); overlapping bills contain identical rows; planted events have absolute
  dates with `.labels.json` ground truth; days after yesterday are not generated.
- `scripts/multi_bill_check.py` and `tests/test_multi_bill.py`: several bills uploaded together are
  checked against an independent oracle (evidence: `docs/evidence/multi-bill/`).
- Dashboard day ranges 180 and 365; API `days` limits raised to 730.

### Changed (2026-10-24)
- Anomaly config v2 (`2026.10-v2`): 14-day baseline and z ≥ 3.0 (was 7 days, 2.5). Development
  precision 0.909 → 1.00, independent hold-out unchanged (1.00 / 0.778), sample-data false alarm
  removed. KPI evidence regenerated (`docs/evidence/kpi-results.json`).

### Changed — code clean-up (2026-10-24)
- Removed the original project's Enterprise teasers: `GET /api/v1/intelligence/change-attribution`
  and `POST /api/v1/events/deployments` (always HTTP 402), `scripts/change_intelligence.py`,
  `scripts/spot_optimization.py`, and `enterprise_features_locked` on `GET /`; unused
  `FocusExportResponse` model.
- One shared `ingest_csv_file()` replaces four copies of "read CSV and ingest" in the scripts.
- The simulated company for own-date bills moved from 250 lines of code to `data/sample-estate.csv`
  (`scripts/period_bills.py` 460 → 207 lines, output byte-identical).
- New-feature API handlers are plain FastAPI sync functions instead of async wrappers around a
  thread pool (same behaviour); duplicate console-encoding code in scripts removed.
- Verified: generated bills, sample report, multi-bill check, public-sample check and 15 API
  responses are identical before and after (except the removed teaser list); 208 tests pass.
- `docs/code-structure.md`: what each module does and how they connect.

### Fixed — found by a fresh install from the ZIP (2026-10-24)
- `ruff` scanned the virtual environment (41,068 errors and a ruff crash) in a copy without
  `.git`, because `exclude` replaced ruff's default excludes. Now `extend-exclude`.
- `run_checks.ps1` ended with `EXIT CODE 0` even when ruff failed. Every step is now checked;
  any failing step makes the run fail and is listed by name.
- Starting the dashboard before setup had finished gave `No module named uvicorn`. All
  launchers now check that setup is complete and say what to do; `run_checks.cmd` says
  when it has finished and shows the result.

### Fixed (2026-10-24)
- A line re-sent in a later bill with a different amount was added on top of the first version
  (conflicts were only checked inside one bill). It is now quarantined as
  `conflicting_duplicate_across_batches`; the same line with the same amount (within 0.01) is a
  duplicate and counted once, also across USD and INR exports.
- An idle finding that needs review now supersedes a right-sizing suggestion for the same resource,
  so "potential" savings no longer count that resource twice.
- Overview "last N days vs prior" compared against a half-empty earlier window when the data
  covered less than 2×N days (it showed +98% on the Q2 sample). Windows now shrink to fit the
  data (`window_shortened_to_fit_data`).

### Added — sample-data acceptance (instructor-approved, 2026-10-16)
- `data/demo/`: the four generated sample bills, approved by the instructor as reference data.
- `scripts/sample_data_report.py` + `run_sample_report.cmd`: one-click HTML/Markdown/JSON report
  with the KPI table, spend, anomalies (scored against the planted scenarios), forecast,
  back-test and recommendations. `run_demo.cmd`: dashboard with the Q2 sample preloaded.
- `config/finops-engine.sample.json` (`2026.10-v1-sample`): $1/day anomaly floor for small bills.
- Dashboard deep link `/dashboard?source=ingested&days=120`.

### Fixed (2026-10-16)
- `ingestion.accepted_usage_units` was never consulted by the validator; configured units
  (now also `GB-Seconds`, `Invocations`, and `GB-Month` as a synonym) no longer raise
  `unrecognised_usage_unit`.

### Fixed — found by testing the user's generated sample bills (2026-10-16)
- Anomaly detection now alerts on a step change off a **flat baseline** (std = 0, typical of
  fixed-price resources). z was forced to 0 there and Isolation Forest cannot flag a level
  that persists, so a GCP compute cost doubling overnight was never alerted at any
  threshold. Alerts carry `evidence.flat_baseline`; z stays finite. KPI-3 unchanged.

### Added (2026-10-16)
- `finops_engine/ingestion/utilization_column.py` and `scripts/utilization_from_billing.py`:
  turn a per-row utilisation column (e.g. `ResourceUtilizationPercent`) into utilisation
  samples, optionally uploading billing + samples to a running API (`--post`). Treated as
  CPU %, memory unknown, daily-value percentiles marked as proxies; storage and serverless
  are skipped with a reason.

### Fixed — found by validating on the FinOps Foundation public FOCUS 1.0 sample
- Literal `NULL` strings are now treated as missing values. 868 rows had been attributed
  to a fake resource called "NULL".
- `Id`, SKU, description, charge class and quantity now distinguish line items in
  duplicate and conflict detection. 38 legitimate rows had been dropped and 75 quarantined.
- Sign-consistent usage reversal rows (negative cost and quantity) are now accepted. The
  accepted total had been +80% too high.
- Breakdown shares now use gross spend. A negative "unattributed share" had appeared.
- Anomaly detection has an absolute materiality floor (`min_absolute_excess_usd`, default
  $5/day). Cent-level noise had produced 21 false alerts.
- Added `scripts/validate_public_sample.py`, `run_public_sample.cmd`, `run_dashboard.cmd`
  (port 8765) and `check_dashboard.cmd`.

### Fixed
- mypy 2.4.0 `arg-type` errors (30) in `optimization/{rightsizing,idle,commitments}.py`:
  the shared Recommendation kwargs dicts are now annotated `dict[str, Any]`.

### Changed
- `GET /api/v1/recommendations/rightsizing` now returns real recommendations instead of
  HTTP 402; `scripts.rightsizing` / `scripts.export_recommendations` now work.
- Connector gateway wraps provider calls with timeouts/retries/breaker; metrics-refresh
  failures are reported as component health instead of being swallowed.
- AWS/GCP/Azure connector tests skip with a visible reason when the cloud SDK is absent.
- Grafana panels that referenced the non-existent `finops_potential_savings_usd` metric
  now use `finops_estimated_monthly_savings_usd`.

### Added
- `GET /` health response now includes an `enterprise_features_locked` list
  advertising the locked Enterprise capabilities (Self-Healing IaC, ChatOps
  approvals, multi-cloud/SaaS visibility, license metering).
- Regression test asserting `finops_engine.__version__` matches the version
  declared in `pyproject.toml`.

### Fixed
- `finops_engine.__version__` was stuck at `1.1.0`; it now reports `1.3.0` to
  match the packaged version.
- Added the runtime `cachetools` dependency to `pyproject.toml` so `pip install .`
  includes it (previously only listed in `requirements.txt`).
- Removed unused `PyJWT` and `cryptography` pins from `requirements.txt`.

### Changed
- `.env.example` no longer references a non-existent `FINOP_LICENSE_KEY`; it now
  documents `FINOP_ALLOW_ANONYMOUS`.
- README configuration table corrected (`FINOP_MOCK_MODE` defaults to `true`,
  added `FINOP_ALLOW_ANONYMOUS`, clarified `FINOP_CORS_ORIGINS`) and the
  Enterprise roadmap entries expanded.

## [1.3.0] - 2026-08-29

### Added
- Change Intelligence and Deployment-to-Cost Attribution engine.
- Automatic correlation between infrastructure deployment events (commits, authors, diffs) and FOCUS spend shifts.
- REST endpoints: `POST /api/v1/events/deployments`, `GET /api/v1/events/deployments`, and `GET /api/v1/intelligence/change-attribution`.
- CLI tool `scripts.change_intelligence` for command-line attribution reports.
- Comprehensive unit and integration test suite for event schema and correlation accuracy.

## [1.2.0] - 2026-08-28

### Added
- Zero-Config Universal Zombie Spend Detection for Pro & Enterprise tiers.
- Cross-correlates FOCUS 1.0 telemetry to find provisioned resources with zero activity metrics.
- Agentless detection across AWS, GCP, and Azure without requiring CloudWatch/Datadog IAM permissions.

## [1.1.0] - 2026-08-28

### Added
- Domain exception hierarchy (`FinOpsError`, `ConnectorError`, `DataFetchError`)
- Mocked test suites for the AWS, GCP, and Azure connectors
- Full coverage reporting for all three live connectors
- Cryptographic (RS256) JWT licensing model for Pro and Enterprise features
- In-memory `cachetools` layer to prevent cloud API rate-limiting on cost fetches
- Streaming JSON responses via FastAPI for the FOCUS export endpoint

### Changed
- Bumped dependencies to current stable releases (Python 3.10 compatible):
  FastAPI, Pydantic, pandas, numpy, scipy, scikit-learn, boto3,
  google-cloud-bigquery, azure-mgmt-costmanagement, azure-identity,
  prometheus-client, and dev tooling (ruff, mypy, pytest, httpx)
- Removed unused dependencies (google-cloud-billing, google-cloud-storage,
  azure-storage-blob, requests, python-dateutil)
- Updated Terraform provider constraints (aws ~> 6.0, google ~> 7.0, azurerm ~> 4.0)
  and Docker image tags (Prometheus 3.x, Grafana 13.x, cAdvisor 0.60.x)
- Typed response models, pagination, env-gated mock mode, and API-key auth
- Fail-closed multi-provider gateway and timezone-aware timestamps
- Rightsizing engine and forecast prediction intervals rebuilt
- Slimmed the Docker runtime image to a non-root user
- Transitioned blocking I/O (ML models and Cloud SDKs) to `fastapi.concurrency.run_in_threadpool`
- Multi-cloud fetching now supports partial-success (fail-open) on individual provider outages

### Fixed
- Azure `QueryDefinition` now sends the required `type` and `datetime` time periods
- Azure/GCP malformed-row handlers no longer reference unbound variables
- Savings metrics summed per provider/category instead of overwritten
- Timing-safe API-key comparison and OpenAPI security scheme documentation

### Removed
- Duplicate CLI tooling consolidated into the `scripts` package

## [1.0.0] - 2025-03-16

Initial release with FOCUS-aligned cost aggregation, anomaly detection,
forecasting, rightsizing, and Prometheus/Grafana monitoring across AWS,
GCP, and Azure.
