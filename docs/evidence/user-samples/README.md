# User-generated sample bills (2026-10-16)

**Label: GENERATED test data, not a real bill.** Later approved by the instructor as the
project's reference data and moved to `data/demo/`; see `../sample-data/` for the final report. The user wrote
four pandas scripts that generate FOCUS-style daily bills, each with a deliberately planted
problem. Copies are committed in `data/demo/`.

| File (SHA-256 prefix) | Period | Planted scenario |
|---|---|---|
| `focus_compliant_demo_q2.csv` (`2b9abdfeb1b9`) | 1 Apr – 30 Jun | 12 resources; serverless volume random; CPU 50–80% weekdays, 10–40% weekends |
| `focus_bill_jan_mar.csv` (`1e3063d7f35c`) | 1 Jan – 31 Mar | AWS serverless 15–25× spike and CPU surge, 20–25 Feb |
| `focus_bill_mar_may.csv` (`37d3d889e29e`) | 1 Mar – 31 May | Storage grows 1.5% of its start value every day (never cleaned up) |
| `focus_bill_may_june.csv` (`4684ebdb6913`) | 1 May – 30 Jun | GCP compute doubles overnight on 10 June ($3.60 → $7.20/day) |

## Results (config `2026.10-v1`, after the fix below)

| File | Accepted / quarantined | Total | Anomalies ($5 floor) | Anomalies ($1 floor) | Forecast MAPE (7-day hold-out) | Estimated savings |
|---|---|---|---|---|---|---|
| demo Q2 | 1,092 / 0 | $11,900.39 | 28 Apr Cloud Functions | same | 13.37% (did not beat naive) | $307.66/mo (7.91%) |
| Jan–Mar | 1,080 / 0 | $2,347.88 | none | none | 0.58% | $157.09/mo (20.05%) |
| Mar–May | 828 / 0 | $5,388.01 | none | none | 0.20% | $129.08/mo (6.20%) |
| May–Jun | 549 / 0 | $2,134.35 | none | **10 Jun GCP_Compute** | 0.86% (did not beat naive) | $193.53/mo (17.79%) |

Per-provider totals match a plain pandas sum of each CSV to the cent. All savings are
commitment suggestions at ASSUMED discount rates. Utilisation samples derived from
`ResourceUtilizationPercent` (6 per file) all have CPU p95 ≥ 57%, so no right-sizing or idle
recommendation is raised; storage and serverless resources are skipped by design.

## What the scenarios showed

- **Bug found and fixed:** a step change off a perfectly flat baseline was never alerted,
  at any threshold (std = 0 forced z = 0, and Isolation Forest cannot flag a level that
  persists). Now detected; regression tests in `tests/test_evaluation.py`. The 10-June jump
  (+$3.60/day) is still below the default $5/day floor, by design.
- **Jan–Mar spike:** the generator prices serverless at $0.00002 × ~26 units, so the
  "spike" moves cost from $0.0005 to $0.013/day. It is immaterial in dollars and correctly not
  alerted. The CPU surge does not change cost.
- **Mar–May leak:** gradual growth is not an anomaly in this detector (documented
  limitation); it is visible in the Trends view.
- **Uploading several scenarios together** mixes resources under the same service names and
  overlapping months, which creates artificial jumps (e.g. 1 March). Load one scenario at a
  time; restart the server to clear the in-memory store.
- **No `SubAccountId` column:** account attribution is 0% for all files.
