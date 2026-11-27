# KPI Results

Source: `docs/evidence/kpi-results.json`, written by
`python -m scripts.acceptance_harness --baseline-code-dir <checkout of baseline commit> --reps 30`
on **2026-10-24** (rerun after the detector baseline change; first run 2026-10-12).

**Environment:** Linux x86_64, 2 vCPU cloud workspace, CPython 3.13.16, pandas 3.0.5,
scikit-learn 1.9.1, FastAPI 0.142.2. App 1.3.0, engine config `2026.10-v2` (USD, `config/finops-engine.usd.json`).

**Data:** SYNTHETIC versioned fixtures (`tests/fixtures`, generator `benchmark-v1`). No real
billing data, cloud account, sandbox, workload trace or Q2 baseline was available.

> Value classes used below: **Measured** = produced by this run.
> **Simulated** = measured on synthetic data, so it says nothing about real savings.
> **Blocked** = cannot be computed honestly with the inputs supplied.

| KPI | Measured value | Baseline / comparison | Formula | Target | Status |
|---|---|---|---|---|---|
| KPI-1 Estimated savings | 30.45 % of reference monthly cost ($31,413.84 of $103,156.14/month; actionable only). A further $12,335.71/month needs review and is **not** counted. | Q2 reference **not supplied** | Σ actionable estimated monthly savings ÷ (trailing-30-day billed cost ÷ days × 30) × 100 | ≈ ≥ 5 % vs Q2 reference | **Simulated**. vs-Q2: **Blocked** |
| KPI-2 Forecast accuracy | Dev hold-out (2026-06-17 to 06-30): MAE $101.84, RMSE $136.27, MAPE 3.02 %, 95 % PI coverage 100 %. Independent hold-out set: MAE $133.85, RMSE $173.96, MAPE 3.88 %, coverage 92.9 % | Naive 7-day mean: MAE $106.29 (dev) / $152.79 (hold-out). Naive last value: $106.29 / $150.85 | MAE, RMSE, MAPE on the last 14 held-out days (MAPE valid: all actuals > 0) | "Measure and report against approved reference data" | **Measured (synthetic)**. Approved reference data not supplied |
| KPI-3 Anomaly precision | Dev: P = 1.00, R = 1.00 (10 alerts, 0 FP). Independent hold-out: P = 1.00, R = 0.778 (7 alerts, 2 FN). Config v1 (7-day baseline, z ≥ 2.5) gave dev P = 0.909; v2 uses a 14-day baseline and z ≥ 3.0 | Pre-change rule (no materiality gate) on the same fixtures: P = 0.556 (dev) / 0.500 (hold-out) | TP ÷ (TP + FP) against injected-label oracle `anomaly_labels*_v1.json` | Compute from versioned fixtures + independent ground truth | **Measured (synthetic)** |
| KPI-4 Recovery rate | 20 / 20 = 1.00 (detection 20 / 20). Max fault-to-recovery 0.40 s with breaker reset 0.2 s | Pre-change code had no breaker or recovery tracking | Recovered faults ÷ injected faults (timeout, exception, breaker-open, flapping × 5) | Measure recoveries after fault injection | **Measured (local simulated faults)** |
| KPI-5 Service error rate | 0.0 (0 / 135 requests) | Pre-change code, same workload and machine: 0.0 (0 / 135) | 5xx ÷ requests, workload `api-workload-v1` | ≈ ≤ 0.8 × Q2 reference | **Measured**. Ratio undefined (0/0). vs-Q2: **Blocked** |
| KPI-6 p95 latency | 64.65 ms overall (p50 20.25, p99 76.89) | Pre-change, same machine/workload: 1,488.63 ms (ratio 0.043) | Nearest-rank p95 of 135 sequential in-process requests | ≈ ≤ 0.80 × Q2 reference | **Measured vs pre-change proxy**. vs-Q2: **Blocked** |

## Sample-data acceptance (instructor-approved reference data, 2026-10-16)

No real cloud bills exist for this college project. The course instructor approved generated
sample data as the reference data. The **Q2 reference** is `data/demo/focus_compliant_demo_q2.csv`
(1 Apr – 30 Jun 2026). Three more files carry planted scenarios used as ground truth
(`data/demo/README.md`). Produced by `run_sample_report.cmd` → `scripts/sample_data_report.py`
with config `2026.10-v2-sample-inr` (₹95/day anomaly floor for these small bills). Full output:
`docs/evidence/sample-data/report.html`.

| KPI | Measured on the sample data | Target | Result |
|---|---|---|---|
| KPI-1 | 7.91 % (₹29,211.91 of ₹3,69,263.97/month = $307.66 of $3,889.04 at ₹94.95/$; actionable commitment suggestions only) | ≥ 5 % of Q2 reference | **Met** |
| KPI-2 | Back-test MAPE (7-day hold-out): Jan–Mar 0.58 %, Mar–May 0.20 %, May–Jun 0.86 %, Q2 13.37 % | Measure against approved reference data | **Reported**. On May–Jun and Q2 the naive "repeat last value" forecast was slightly better |
| KPI-3 | Precision 1.00 (1 of 1 alert matches a planted event); recall 1 of 2 planted spike/step events | Against independent ground truth | **Reported**. Missed: the Jan–Mar serverless spike changes cost by only $0.01/day. With config v1 there was also a false positive (28 Apr Cloud Functions, precision 0.50); the v2 14-day baseline removed it |
| KPI-4 | 20/20 injected faults recovered | Measure recoveries | **Reported** |
| KPI-5 | 0 / 135 server errors vs 0 / 135 for the original code | ≤ 0.8 × reference | **Met** (no errors in either) |
| KPI-6 | p95 64.65 ms vs 1,488.63 ms for the original code (ratio 0.043) | ≤ 0.80 × reference | **Met** |

For KPI-5 and KPI-6 the reference is the original v1.3.0 code measured on the same machine
and workload (`kpi-results.json`); a service-level Q2 measurement cannot come from a bill.
Savings use ASSUMED commitment discount rates and are estimates, not realised savings.

## Reproduction on the pinned stack (Windows PC, 2026-10-16)

`docs/evidence/windows/kpi-results-windows.json`. Environment: Windows 11, 12 CPUs, Python 3.13.7,
pandas 2.3.3, scikit-learn 1.7.2. Final code only; the pre-change benchmark was not repeated
there.

| KPI | Windows result | Same as Linux run? |
|---|---|---|
| KPI-1 | 30.45 % | identical |
| KPI-2 | MAPE 3.02 % (dev) / 3.88 % (hold-out) | identical |
| KPI-3 | v1: P 0.909 / R 1.00 (dev); P 1.00 / R 0.778 (hold-out). v2 (run 6, 2026-10-24): P 1.00 / R 1.00 (dev); P 1.00 / R 0.778 (hold-out) | identical for each config version |
| KPI-4 | 20 / 20, max 0.42 s | same rate |
| KPI-5 | 0 / 135 | identical |
| KPI-6 | p95 65.62 ms (run 2), 80.96 ms (run 3, after the real-data fixes), 133.58 ms (run 6, 2026-10-24). The first run gave 104.96 ms. | different hardware, so not comparable to the Linux numbers |

The detection, forecast and savings results do not depend on the pandas 2 vs 3 or
scikit-learn 1.7 vs 1.9 differences.

## How to read KPI-6

The overall improvement comes **entirely** from the anomaly endpoint. The new
detection-result cache makes repeated calls fast (p95 3,213 ms → 3 ms). A cold first call
still takes about 1.5 s: 1,541 ms before vs 1,565 ms after.

The other endpoints are equal or slightly slower after the change. This is within the noise
of one run on a shared 2-vCPU host, plus the new telemetry middleware.

| Endpoint p95 (ms) | Pre-change | Final |
|---|---|---|
| health `/` | 83.42 | 38.05 |
| cost summary | 70.30 | 76.89 |
| focus export | 14.21 | 64.65 |
| forecast | 22.87 | 24.18 |
| anomalies (warm) | 3,213.14 | 2.77 |

Latency is measured in-process with the FastAPI TestClient, so it excludes network,
uvicorn and TLS. Each figure comes from one run, and repeat runs vary by tens of
milliseconds. Neither number is a production SLO measurement.

## AC-1 … AC-4

| Criterion | Status | Evidence / reason |
|---|---|---|
| AC-1 representative / sandbox validation and realised savings | **Partial**: validated on anonymised real-world FOCUS data (`public-sample/README.md`). Spend matches the oracle exactly and 5 real-data bugs were found and fixed. Sandbox and realised savings remain **blocked**. | No sandbox or representative workload. Realised savings are `not_measured`: nothing was implemented and there is no post-change billing. `realized_savings()` is implemented and tested on fixtures. |
| AC-2 boundary/failure operation, billing complexity, attribution, safe recommendations | **Met on fixtures** | NT-1 to NT-5: 38 / 38 passed (`negative-test-results.md`) |
| AC-3 independent acceptance evidence, representative workload, fault campaign, versioned config, workload traces | **Partial** | Fault campaign, versioned config and workload traces (`workload-trace-*.csv`) exist. Evidence was produced by the implementer, not an independent party, and the workload is synthetic. |
| AC-4 same versions, workload, hardware for reference vs final | **Met for the pre-change proxy only** | Both benchmarks ran in one session on the same host with the same interpreter and workload file. The Q2 comparison is not possible. |

With the instructor-approved sample data, AC-1 is met for sample-data validation (see the
section above); sandbox validation and realised savings still need a live account. For real
bills, fill in `q2-baseline-template.json` and rerun the harness on the same hardware envelope.
