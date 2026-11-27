# Evidence Manifest

Every file listed here was **actually generated** during this work on 2026-10-12 in the
Linux cloud workspace (2 vCPU, CPython 3.13.16). Local absolute paths were redacted to
`<repo>`, `<tmp>` and `<site-packages>`.

| File | What it is | Produced by |
|---|---|---|
| `pytest-output.txt` | Full test run: 159 passed, 3 skipped | `pytest tests -p no:cacheprovider --junitxml=docs/evidence/pytest-junit.xml` |
| `pytest-junit.xml` | Machine-readable version of the same run | same |
| `negative-tests-output.txt` | NT-1…NT-5 verbose run: 38 passed | `pytest tests/negative -v -p no:cacheprovider` |
| `negative-test-results.md` | NT matrix: injected fault, expected, actual, pass | written from the run above |
| `static-checks.txt` | ruff, ruff format and mypy output (all clean) | `ruff check .; ruff format --check .; mypy finops_engine` |
| `kpi-results.json` | All harness measurements: spend oracle, anomaly and forecast (dev + hold-out), savings ledger, fault campaign (20 trials), benchmarks, KPI table | `python -m scripts.acceptance_harness --baseline-code-dir <baseline worktree> --reps 30` |
| `KPI-results.md` | Human-readable KPI table with value classes | written from `kpi-results.json` |
| `bench-final.json`, `bench-baseline.json` | Latency/error benchmark of final vs pre-change code, same host and workload | harness → `scripts/benchmark_api.py` |
| `workload-trace-final.csv`, `workload-trace-baseline.csv` | Per-request trace (seq, endpoint, status, ms) | same |
| `q2-baseline-template.json` | **Template only. Contains no measurements.** | hand-written |
| `screenshots/light-*.png` (7), `screenshots/dark-{overview,forecast}.png` | Dashboard views rendered by headless Chromium against the running app on **SYNTHETIC mock data** | Playwright script in the implementer's scratch space (not part of the repo) |

## Windows / pinned-stack evidence (`docs/evidence/windows/`, 2026-10-16)

Produced on the user's Windows 11 PC by `run_checks.cmd`, which runs `scripts\run_checks.ps1`.
The user's folder path is redacted to `<repo>`.

| File | Contents |
|---|---|
| `run-checks-run1-summary.txt` | Run 1 summary: mypy 2.4.0 found 30 typing errors; ruff, tests (170 passed) and negative tests (38 passed) were green |
| `run-checks-console-run2.txt` | Full console of run 2 after the fix: pip, ruff, format, mypy clean, pytest, negative tests, harness, exit code 0 |
| `pytest-output-windows.txt`, `pytest-junit-windows.xml` | Run 2 pytest: 170 passed, 0 skipped |
| `kpi-results-windows.json`, `bench-final-windows.json` | Run 2 harness output (final code only) |

| `run-checks-console-run3.txt` | Run 3 after the real-data fixes: 176 passed, 38 negative passed, static checks clean, exit 0 (the run-3 pytest/KPI files replaced run 2's) |
| `run-checks-console-run4.txt` | Run 4 after the flat-baseline fix and utilisation converter: 192 passed, 38 negative passed, static checks clean, exit 0 (pytest files now from run 4) |
| `fresh-install-zip-copy-run.txt` | Fresh install from the 2026-10-24 ZIP into a new folder on the user's PC (new `.venv`, libraries downloaded): first attempt exposed ruff linting `.venv` (41,068 errors) while still reporting exit 0; after the fix: every step passed, 218 tests, 38 negative, exit 0 |
| `run-checks-console-run6.txt` | Run 6 (2026-10-24): 218 passed, 38 negative passed, static checks clean, harness with config v2, exit 0. `pytest-*-windows.*`, `kpi-results-windows.json` and `bench-final-windows.json` are now from run 6 |
| `run-checks-console-run7.txt` | Run 7 (2026-10-24): after the code clean-up. 219 passed, 38 negative passed, ruff/format/mypy clean, harness completed, exit 0 |
| `run-checks-console-run5.txt` | Run 5 with the sample-data report and unit fix: 195 passed, 38 negative passed, static checks clean, exit 0 |

## Anonymised real-world data (`docs/evidence/public-sample/`, 2026-10-16)

| File | Contents |
|---|---|
| `README.md` | Dataset provenance and licence (FinOps Foundation FOCUS 1.0 sample, CC BY 4.0), results, and the 5 bugs it exposed |
| `report.json` | Output of `python -m scripts.validate_public_sample` on the user's PC |

The data file itself (`data/samples/focus_sample_10000.csv`) is git-ignored.

## Multiple bills uploaded together (`docs/evidence/multi-bill/`, 2026-10-24)

| File | Contents |
|---|---|
| `README.md`, `report.json` | `scripts/multi_bill_check.py`: three generated bills (USD and INR, overlapping March) plus a re-upload; totals vs independent oracle, duplicates, planted anomalies found, forecast, recommendations |

## Instructor-approved sample-data report (`docs/evidence/sample-data/`, 2026-10-16)

| File | Contents |
|---|---|
| `report.html`, `report.md`, `report.json` | Output of `scripts/sample_data_report.py` on `data/demo/*.csv`: KPI table and per-file results, **amounts in INR** (₹94.95/$). Regenerated 2026-10-24 in the cloud workspace after the rupee change; the 2026-10-16 USD version was produced on the user's Windows PC (same KPI results) |

## User-generated sample bills (`docs/evidence/user-samples/`, 2026-10-16)

| File | Contents |
|---|---|
| `README.md` | Four generated scenario bills (not committed): results, the flat-baseline bug they exposed, and why they are not a Q2 baseline |

## Fixtures (inputs, versioned)

`tests/fixtures/*_v1.*` and `tests/fixtures/negative/*` (see `docs/testing-and-acceptance.md`).

## Evidence produced at runtime (not committed)

When the service or tests run, `FINOP_EVIDENCE_DIR` (default `output/evidence`) receives:
- `ingestion/batch-<id>.json`: the batch report plus quarantined raw rows
- `health-events.jsonl`: failure, degraded, recovered and safe-mode transitions

This directory is git-ignored because quarantined rows can contain real billing data.

## Not generated (explicitly)

- **Coverage report:** not requested (pytest-cov is installed on the Windows `.venv`; run `make cov` or `pytest --cov=finops_engine`).
- **CI run, Docker image build, Kubernetes deployment:** not executed.
- **Live cloud or OpenCost measurements, real-bill savings, realised savings, Q2
  comparisons:** no access or data. See `KPI-results.md`.
- **Video demo:** not recorded. See the checklist in `FINAL_IMPLEMENTATION_REPORT.md`.
