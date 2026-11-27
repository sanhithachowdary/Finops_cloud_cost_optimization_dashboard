# Testing and Acceptance

## Test matrix (actual results, 2026-10-12)

| Suite | Command | Result | Evidence |
|---|---|---|---|
| Baseline, before any change | `pytest tests/` on the unmodified ZIP | 65 passed, **11 failed**. All 11 are connector tests that patch cloud SDKs not installed here. | `docs/gap-analysis.md` |
| Full suite, final | `pytest tests/` | **159 passed, 3 skipped**, 0 failed | `docs/evidence/pytest-output.txt`, `pytest-junit.xml` |
| Mandatory negative tests | `pytest tests/negative -v` | **38 passed** | `negative-tests-output.txt`, `negative-test-results.md` |
| Lint | `ruff check .` | pass | `static-checks.txt` |
| Format | `ruff format --check .` | pass (94 files) | `static-checks.txt` |
| Types | `mypy finops_engine` | pass (40 files) | `static-checks.txt` |
| Acceptance / KPI harness | `python -m scripts.acceptance_harness --baseline-code-dir <baseline checkout>` | completed in 52 s | `kpi-results.json`, `KPI-results.md` |
| Flakiness check | negative + ingestion tests, 5 consecutive runs | 5/5 green | session log |

### Verification on the user's Windows PC with the pinned stack (2026-10-16)

`scripts\run_checks.ps1` (launched via `run_checks.cmd`). Environment: Windows 11, 12 CPUs,
Python 3.13.7, packages from `requirements-dev.txt` at the **exact pins** (pandas 2.3.3,
numpy 2.2.6, scikit-learn 1.7.2, scipy 1.15.3, mypy 2.4.0, ruff 0.16.10, boto3,
google-cloud-bigquery, azure-mgmt-costmanagement …).

| Run | ruff / format | mypy 2.4.0 | pytest | Negative tests | Harness | Evidence |
|---|---|---|---|---|---|---|
| Run 1 | pass / pass (94 files) | **30 errors** in `optimization/{rightsizing,idle,commitments}.py` | **170 passed, 0 skipped** | 38 passed | completed | `docs/evidence/windows/run-checks-run1-summary.txt` |
| Fix | Annotated the shared `base` kwargs dicts as `dict[str, Any]`. Typing only, no behaviour change. mypy 2.3.1 in the workspace had not reported this. | | | | | commit on the feature branch |
| Run 2 | pass / pass (94 files) | **no issues (40 files)** | **170 passed, 0 skipped** (105.7 s) | **38 passed** | completed | `windows/pytest-output-windows.txt`, `pytest-junit-windows.xml`, `run-checks-console-run2.txt`, `kpi-results-windows.json` |

| Run 3 (after the real-data fixes) | pass / pass (96 files) | no issues | **176 passed, 0 skipped** | **38 passed** | completed | `windows/run-checks-console-run3.txt` |
| Run 4 (after the flat-baseline fix and utilisation converter) | pass / pass (101 files) | no issues | **192 passed, 0 skipped** | **38 passed** | completed | `windows/run-checks-console-run4.txt` |
| Fresh install from the ZIP (new folder, new `.venv`) | pass / pass (114 files) after the `extend-exclude` fix; the first attempt linted `.venv` | no issues | **218 passed, 0 skipped** | **38 passed** | completed | `windows/fresh-install-zip-copy-run.txt` |
| Run 6 (2026-10-24: rupees, own-date bills, multi-bill, config v2) | pass / pass (114 files) | no issues | **218 passed, 0 skipped** | **38 passed** | completed | `windows/run-checks-console-run6.txt` |
| Run 7 (2026-10-24: code clean-up, Enterprise teasers removed) | pass / pass (113 files) | no issues (42 files) | **219 passed, 0 skipped** | **38 passed** | completed | `windows/run-checks-console-run7.txt` |
| Run 5 (sample-data report, unit fix) | pass / pass (104 files) | no issues | **195 passed, 0 skipped** | **38 passed** | completed | `windows/run-checks-console-run5.txt` |

### Multiple bills, own dates, rupees (2026-10-24, cloud workspace)

| Suite | Command | Result | Evidence |
|---|---|---|---|
| Full suite | `pytest tests/` | **207 passed, 3 skipped** (cloud SDK modules) | session log |
| Multiple bills together | `python -m scripts.multi_bill_check` | 6/6 checks pass; 3/3 planted anomalies found, precision 0.75 | `docs/evidence/multi-bill/` |
| KPI harness with config v2 | `python -m scripts.acceptance_harness --baseline-code-dir <baseline>` | dev P 1.00 / R 1.00, hold-out P 1.00 / R 0.778; p95 64.65 ms vs 1,488.63 ms | `kpi-results.json`, `KPI-results.md` |
| Sample-data report (₹) | `python -m scripts.sample_data_report` | KPI-1 7.91 % met; KPI-3 precision 1.00 | `docs/evidence/sample-data/` |

New tests: `tests/test_multi_bill.py` (overlapping bills identical, totals vs oracle across USD and
INR bills, planted anomalies found, restated lines quarantined), `tests/test_currency.py`,
`tests/test_generate_sample_bills.py` (reproduces `data/demo/` exactly).

### Validation on anonymised real-world data (2026-10-16)

`python -m scripts.validate_public_sample data\samples\focus_sample_10000.csv`, run on the
FinOps Foundation public FOCUS 1.0 sample (10,000 rows, CC BY 4.0):
- Spend by provider equals an independent plain-CSV oracle (**pass**).
- Cost reconciles exactly.
- It exposed **5 bugs**, now fixed with regression tests. Details are in
  `docs/evidence/public-sample/README.md`.

On the pinned stack the 3 cloud-SDK connector modules ran and passed (11 additional test
cases), which is why the count is 170 rather than 159 + 3 skipped. The only warning is a
third-party deprecation notice: FastAPI's TestClient suggests `httpx2`.

In the cloud workspace, the **3 skipped** are the AWS, GCP and Azure connector modules. They need the real cloud
SDKs (boto3, google-cloud-bigquery, azure-mgmt-costmanagement), which could not be
installed here. They now skip with a visible reason instead of failing misleadingly. On the
pinned stack (`pip install -r requirements-dev.txt`, as in CI) they run normally.

**Not run in this work:**
- a coverage report (pytest-cov was installed on Windows but `--cov` was not requested)
- the GitHub Actions workflow
- Docker or Compose build
- Python 3.10, 3.11 or 3.12 test runs. The Windows PC has Python 3.13.7; Python 3.10 was only
  used for a syntax compile and the fixture-determinism check.

**Test environment caveat:** Python 3.13.16 with pandas 3.0.5, NumPy 2.5.3, scikit-learn
1.9.1 and SciPy 1.18.1, which are newer than the pins (Python 3.10, pandas 2.3.3, numpy
2.2.6, sklearn 1.7.2). FastAPI 0.142.2, pydantic 2.13.5, pydantic-settings 2.15.0,
cachetools 7.2.0 and prometheus-client 0.26.0 match the pins exactly; they were built from
the official GitHub tags with your approval, because PyPI was blocked. **This caveat is
resolved by the Windows run above, which used the exact pins.**

## Test inventory

| File | Covers |
|---|---|
| `tests/test_spend_analytics.py` | FR-2 against the hand-computed oracle, UTC timezone boundary, ISO weeks, top-N folding, empty data, pure-Python recomputation over 1,560 rows |
| `tests/test_ingestion.py` | Provenance, idempotency, cross-file duplicates, schema version, FX, credits, large/zero values, concurrent ingestion (8 threads → 1 batch), evidence files, OpenCost adapter |
| `tests/test_evaluation.py` | Alert contract, precision/recall on dev and independent hold-out sets, determinism, back-test, insufficient history |
| `tests/test_optimization.py` | Outcome oracle per resource, recommendation contract, savings ledger arithmetic, overlap controls, commitments gating, realised savings |
| `tests/test_engine_config.py` | Config validation (unknown key, range, bad JSON, missing explicit file); shipped file matches the schema |
| `tests/test_api_extended.py` | End-to-end ingest → analytics → anomalies → forecast → recommendations → savings → ops; query validation; auth boundary; dashboard headers; latency metrics |
| `tests/negative/*` | NT-1 … NT-5 (see `docs/evidence/negative-test-results.md`) |
| Original 10 files | Unchanged, except the 3 connector modules now skip when the SDK is absent |

## Fixtures (versioned, SYNTHETIC)

| File | Origin | Used for |
|---|---|---|
| `billing_small_v1.csv` + `.expected.json` | Hand-written; expected values computed by hand | FR-2 oracle |
| `billing_benchmark_v1.csv` + `anomaly_labels_v1.json` | `scripts/generate_fixtures.py` (seed 20260630, 120 days ending 2026-06-30) | Development set: detection, forecast, savings |
| `billing_holdout_v1.csv` + `anomaly_labels_holdout_v1.json` | Same generator, seed 7, different injection plan. Never used for tuning. | Independent evaluation |
| `utilization_benchmark_v1.json` | Generator; `expected` outcomes derived by hand from the documented policy | Recommendation oracle |
| `opencost_allocation_v1.json` | Hand-written in the OpenCost `/allocation` response shape | OpenCost adapter, NT-2 |
| `negative/nt1_problematic_billing.csv`, `negative/nt2_attribution.csv` | Hand-written | NT-1, NT-2 |

Regenerate with `python -m scripts.generate_fixtures`. The output is byte-identical; the
SHA-256 of `billing_benchmark_v1.csv` is `c3da55f1…ce883`.

**Ground-truth independence:** the generator uses only the standard library and does not
import `finops_engine`. Labels come from the injection plan.

**Honesty note:** the detector's 20% materiality gate was chosen after seeing the
development set's false positives. That is why the separate hold-out set exists. It was
evaluated once with the shipped config: precision 1.00, recall 0.78.

## KPI protocol

1. Use the same machine and interpreter for the reference and final runs (AC-4).
   `--baseline-code-dir` points to a checkout of the baseline commit:
   `git worktree add --detach ../baseline main`.
2. Workload `api-workload-v1` (`scripts/benchmark_api.py`): sequential in-process requests
   to the 5 endpoints that exist in both versions, 30 repetitions each (15 for anomalies),
   with 2 warm-up calls per endpoint excluded. The trace is written to
   `workload-trace-*.csv`.
3. Percentiles use the nearest-rank method. Error rate = 5xx ÷ requests.
4. Fault campaign: 4 fault types × 5 trials. A trial counts as recovered when the first
   call after healing succeeds within 5 s and the component returns to `ok`.
5. Relative KPIs (KPI-1, KPI-5, KPI-6) require a Q2 reference. Fill in
   `docs/evidence/q2-baseline-template.json`. Until then they are reported as **blocked**.

## SLOs (NFR-3), from `config/finops-engine.json` → `slo`

| SLO | Target | How measured | Current evidence |
|---|---|---|---|
| p95 API latency | ≤ 1,500 ms | `/api/v1/ops/slo` (in-process ring buffer, ≥ 20 samples) and the Prometheus histogram | Benchmark p95 67 ms (in-process, warm). The cold anomaly call is ~1.5 s, close to the target. |
| Server error rate | ≤ 1% | 5xx ÷ requests | 0 / 135 |
| Recovery time | ≤ 60 s after the dependency heals | `recovered` health events (`recovery_seconds`) | Max 0.40 s in the fault campaign |
| Data integrity | Not in safe mode; reconciliation exact to $0.01 | Batch reconciliation and `mode` | NT-1 reconciliation exact |
| Availability | ≥ 99% | **Not measurable in-process.** Use an external probe on `/healthz` or Prometheus `up`. | Not measured |

The targets are sized for this feasible test environment: a single process on a 2-vCPU host
with synthetic data. Re-baseline them on production hardware.
