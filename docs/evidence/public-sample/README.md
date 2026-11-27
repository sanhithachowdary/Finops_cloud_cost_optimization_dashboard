# Validation on anonymised real-world billing data (public FOCUS 1.0 sample)

| | |
|---|---|
| Dataset | FinOps Foundation `FOCUS-Sample-Data`, file `FOCUS-1.0/focus_sample_10000.csv` (commit `adbdd17`) |
| Source | <https://github.com/FinOps-Open-Cost-and-Usage-Spec/FOCUS-Sample-Data> |
| Licence | CC BY 4.0, © FinOps Foundation. The data file is **not** committed (`data/samples/` is git-ignored). |
| SHA-256 | `0bf58b73123294c4476b648e647cad842935036f67dc4e9a2cc4a6d7497cc2e3` |
| Content | 10,000 hourly FOCUS rows, September 2024. AWS 9,441 rows, Microsoft 491, Oracle 68. Anonymised real data. |
| What it is **not** | It is not this project's Q2 baseline, and the savings figures it produces are not your savings |
| Command | `python -m scripts.validate_public_sample data\samples\focus_sample_10000.csv` (Windows: `run_public_sample.cmd`) |
| Run | 2026-10-16 on the user's Windows 11 PC, pinned stack, exit code 0 → `report.json` |

## Results (from `report.json`)

| Check | Result |
|---|---|
| Ingestion | 10,000 rows in: **9,927 accepted, 73 quarantined, 0 duplicates**. Quarantine ratio 0.73%, so no safe mode. |
| Quarantine reasons | 68 × `unknown_provider` (Oracle is outside the supported AWS/GCP/Azure set). 5 × `negative_usage_quantity` (negative quantity with non-negative cost). |
| Accepted with warnings | 149 Azure reversal rows (negative cost **and** quantity). 20,000 naive timestamps assumed UTC. 1,111 unrecognised units. 33 seconds/minutes → Hours conversions. |
| Reconciliation | accepted $146.240996 + quarantined $5.188945 + duplicates $0 = **$151.429941**, the exact sum of the file's BilledCost |
| Spend vs independent oracle | Engine **AWS $112.17 / Azure $34.07**, exactly equal to a plain-CSV recomputation over the same accepted row Ids → **pass** |
| Period | 30 UTC days (2024-09-01 … 09-30). Net $146.24, of which usage $244.84 and credits −$98.60 |
| Attribution | 868 rows with `ResourceId = NULL`, so **25.4% of gross spend is unattributed** (74.6% attributed). 5 resource ids appear under more than one account and are flagged. |
| Anomalies | 0 alerts. The whole bill is about $5/day, below the $5/day absolute materiality floor. |
| Forecast back-test (7 days) | MAE $9.67, RMSE $16.75. MAPE is correctly reported as undefined because the held-out days contain zero or negative net values. |
| Recommendations | Commitments: 2 `requires_review` (volatile, credits) and 6 `insufficient_data`. Right-sizing and idle were not evaluated because billing exports carry no utilisation. **$0 estimated savings, so nothing was invented.** |

## Bugs this real data exposed (fixed, with regression tests)

| # | Problem on real data | Impact before the fix | Fix | Regression test |
|---|---|---|---|---|
| 1 | Literal `NULL` strings were treated as values | 868 rows attributed to a fake resource called "NULL", flagged as a "conflict" | `NULL`/`None` markers become missing values (`NaN` cost is still malformed) | `test_ingestion.py::test_literal_null_strings_are_missing_values` |
| 2 | Distinct line items in the same hour (different `Id`, SKU or description) were treated as duplicates or conflicts | 38 legitimate rows dropped as duplicates and 75 quarantined | `Id`, `SkuPriceId`, `SkuId`, `ChargeDescription`, `ChargeClass` and `ConsumedQuantity` now distinguish line items when present | `test_distinct_line_items_in_same_hour_are_not_duplicates`, `test_same_line_item_id_is_still_deduplicated` |
| 3 | Azure reversal rows (negative cost and quantity) were quarantined | Accepted total $271.87 instead of the true $151.43 (+80%) | Sign-consistent reversals are accepted and counted. Inconsistent signs are still quarantined. | `test_reversal_row_accepted_but_inconsistent_sign_quarantined` |
| 4 | Shares were computed on net spend | "Unattributed share" came out at −66% | Shares use gross spend (sum of absolute per-record cost) | `test_spend_analytics.py::test_shares_use_gross_spend_when_credits_make_net_small` |
| 5 | No absolute materiality floor on anomalies | 21 alerts on cent-level changes (e.g. $0.45 vs $0.00) | `anomaly.min_absolute_excess_usd` (default $5/day, configurable) | `test_evaluation.py::test_absolute_materiality_floor_suppresses_cent_level_noise` |

After the fixes, the synthetic KPI results are unchanged: dev P 0.909 / R 1.00, hold-out
P 1.00 / R 0.778, and the same forecast and savings figures.
