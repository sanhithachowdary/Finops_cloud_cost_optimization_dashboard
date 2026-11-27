# API and Data Contracts

Interactive OpenAPI: `/docs`. All `/api/v1/*` routes require `X-API-Key` when
`FINOP_API_KEY` is set. `/`, `/healthz` and `/dashboard` are public and carry no spend data.
Errors return `{"detail": ...}`. Unexpected errors return
`{"detail": "Internal server error", "incident_id": "<id>"}` with no stack trace.

**Currency:** all amounts are in the configured reporting currency (INR in the shipped config;
see `setup-and-operations.md`). Field names ending in `_usd` are historical and are kept for
compatibility.

## Endpoints

Removed on 2026-10-24: the Enterprise teasers `GET /api/v1/intelligence/change-attribution` and
`POST /api/v1/events/deployments` (they always returned HTTP 402) and `enterprise_features_locked` on `GET /`.


| Method | Path | Notes |
|---|---|---|
| GET | `/` | Service info (existing) |
| GET | `/healthz` | `{"status","mode","currency"}`. `currency` is the reporting currency of all amounts. Returns 503 when a component has failed. |
| GET | `/dashboard` | Built-in UI (CSP: self only, no CDN) |
| GET | `/metrics` | Prometheus (existing, key-protected) |
| GET | `/api/v1/costs/summary` | Existing. New optional `source`. |
| GET | `/api/v1/costs/focus-export` | Existing |
| GET | `/api/v1/costs/overview` | `days`, `source`, `metric`. Totals, period comparison, provider split, attribution quality. |
| GET | `/api/v1/costs/trends` | `granularity=daily\|weekly\|monthly`, `days`, `metric=billed_cost\|effective_cost`, `split_by_provider`, `source`. UTC buckets. |
| GET | `/api/v1/costs/breakdown` | `dimension=provider\|account\|service\|category\|region\|resource\|workload\|charge_category`, `top_n`, `metric`, `source` |
| GET | `/api/v1/anomalies/detect` | Existing. New optional `source`. Alerts use the extended contract below. |
| GET | `/api/v1/forecast/predict` | Existing. New `source`, `history_days`, plus `status`, `method`, `history_days`, `trend_usd_per_day` in the response. |
| GET | `/api/v1/forecast/backtest` | `holdout_days`, `days`, `source`. MAE/RMSE/MAPE vs naive baselines and interval coverage. |
| POST | `/api/v1/ingest/billing` | Raw body. `format=csv\|json`, `source_name`, `schema_version`. Returns 201 with a batch report. 413 if larger than the limit, 422 if unparseable or on unsupported version. |
| POST | `/api/v1/ingest/opencost` | Raw OpenCost `/allocation` JSON. Optional `provider`, `currency`, `account_id`. |
| POST | `/api/v1/ingest/utilization` | JSON list or `{"samples": [...]}` of `UtilizationSample`. Returns `accepted`, `rejected` and per-item reasons. |
| GET | `/api/v1/ingest/reports` | Quality summary and latest batch reports |
| GET | `/api/v1/recommendations` | `kind`, `status`, `days`, `source` |
| GET | `/api/v1/recommendations/rightsizing` | Right-sizing only. **Changed:** previously HTTP 402 Enterprise teaser. |
| GET | `/api/v1/savings/summary` | Reference cost, estimated vs potential vs realised, formula, overlap controls |
| GET | `/api/v1/ops/health` | Components, mode, breakers, recent events, operator guidance |
| GET | `/api/v1/ops/slo` | Measured p50/p95/p99, error rate, verdict vs targets |
| GET | `/api/v1/ops/quality` | Ingestion quality summary |

`source`: `auto` (default; original behaviour), `mock`, `live` or `ingested`. The
`ingested` source covers the last `days` relative to the newest ingested record, not
relative to today.

## Billing input (FOCUS-aligned)

Both FOCUS column names and this project's snake_case names are accepted.

| Canonical | FOCUS alias | Required | Rule |
|---|---|---|---|
| provider_name | ProviderName | ✔ | AWS / GCP / Azure (common aliases accepted). Otherwise `unknown_provider`. |
| service_name | ServiceName | ✔ | |
| billed_cost | BilledCost | ✔ | Finite number, \|x\| ≤ 1,000,000. Negative only for Credit/Adjustment. |
| effective_cost | EffectiveCost | | Defaults to billed (recorded as a transformation) |
| currency | BillingCurrency | ✔ | Must be `USD`, or listed in `fx_rates_to_base`. Otherwise quarantined. |
| billing_period_start/end | ChargePeriodStart/End | ✔ | ISO-8601. Naive timestamps are treated as UTC (warning). End must be after start. Start no more than 48 h in the future. |
| charge_category | ChargeCategory | | Usage (default) / Purchase / Tax / Credit / Adjustment |
| usage_quantity, usage_unit | ConsumedQuantity, ConsumedUnit | | Unit synonyms normalised. Seconds and minutes converted to Hours. |
| sub_account_id, resource_id, region_id, tags | SubAccountId, ResourceId, RegionId, Tags | | Missing values mean unattributed. Tags may be JSON or `k=v;k2=v2`. |

Value rules found on real exports:
- **Missing values:** the literal strings `NULL` and `None` (any case) count as missing.
  `NaN` as a cost is malformed.
- **Distinguishing line items:** when present, `Id`, `SkuPriceId`, `SkuId`,
  `ChargeDescription`, `ChargeClass` and `ConsumedQuantity` distinguish separate line items in
  duplicate and conflict detection.
- **Reversals:** a Usage row with negative cost **and** negative quantity is accepted as a
  reversal (warning `usage_reversal_row_accepted`). A negative value on only one side is
  quarantined.

Batch-level rules:
- `schema_version` (or `focus_version` in JSON) must be one of `1.0`, `1.0-preview` or `1.1`.
  Otherwise the whole batch is rejected.
- Exact duplicates are counted once, including across batches.
- The same line item reported with different amounts gives `conflicting_duplicate`, and all
  versions are quarantined.

**Batch report fields:**
- Provenance: `batch_id`, `input_sha256`, `source_name`, `source_type`, `schema_version`,
  `config_version`, `received_at`
- Row counts: `rows_received`, `rows_accepted`, `rows_quarantined`, `rows_duplicate`,
  `rows_unparseable_cost`
- Reconciliation: `accepted`, `quarantined`, `duplicate` and `parsed_input` cost
- Findings: `quarantine_reasons`, `warnings`, `transformations`, `out_of_order_rows`
- `attribution`: `attributed_cost_share`, `unattributed_rows`, `conflicting_resource_ids`
- `quarantine_ratio`, `safe_mode_triggered`, `idempotent_replay`

Breakdown `share` and `unattributed_share` are fractions of **gross** spend (the sum of
absolute per-record costs; `share_basis` in the response). `total_cost_usd` stays net.

## UtilizationSample

`resource_id` (required), `provider`, `resource_type` (`vm`, `k8s_workload`, `database`,
`disk`, `ip_address`, `load_balancer`, `other`), `size`, `cpu_avg_pct`, `cpu_p95_pct`,
`mem_avg_pct`, `mem_p95_pct`, `network_mb_per_day`, `requests_per_day`, `observation_days`,
`attached`, `percentiles_are_proxies`, `tags`, `source`.

Percentages must be between 0 and 100 and counts must be ≥ 0. Unknown fields are rejected.

Samples can be derived from a billing CSV that has a per-row utilisation column:
`python -m scripts.utilization_from_billing bill.csv --post http://127.0.0.1:8765`
(`source` = `billing_column:<column>`, `percentiles_are_proxies` = true, memory null).
**`null` means not measured, which is different from `0`.**

## Anomaly alert (extended; original keys unchanged)

- **Original keys:** `date`, `provider`, `service`, `actual_cost_usd`, `expected_baseline_usd`,
  `anomaly_excess_usd`, `z_score`, `severity` (HIGH > $200 excess, MEDIUM > $50, else LOW),
  `root_cause`
- **New identity and status:** `alert_id` (stable hash of day + entity), `status` (`open`)
- **New entity and window:** `affected_entity{provider,service,accounts}`, `window_start`,
  `window_end`
- **New values:** `observed_cost_usd`, `expected_cost_usd`, `score`, `reason`,
  `detection_method`
- **New `evidence`:** `baseline_window_days`, `baseline_points`, `baseline_mean_usd`,
  `baseline_std_usd`, `isolation_forest_outlier`, `z_threshold`, `contamination`,
  `min_relative_excess`, `min_absolute_excess_usd`, `relative_excess`, `flat_baseline`
  (true when the baseline std is ≤ 1% of its mean; Isolation Forest is then not required)
- **New `config_version`**

## Recommendation

`id`, `kind` (`rightsizing`, `idle` or `commitment`), `status`, `resource_id`, `provider`,
`service`, `action`, `reason`, `assumptions[]`, `current_monthly_cost_usd`,
`estimated_monthly_savings_usd`, `confidence` (`high`, `medium`, `low` or `none`),
`limitations[]`, `safe_next_action`, `evidence{}`, `config_version`, `auto_apply` (always
`false`).

| Status | Meaning | Counted in estimated savings? |
|---|---|---|
| actionable | All checks passed | yes |
| requires_review | Plausible, but a safeguard tripped: short window, protected tag, conflict, missing memory, volatility, existing discounts | no (reported as "potential") |
| insufficient_data | A required signal is missing. No savings are claimed. | no |
| rejected | Unsupported resource type or implausible estimate | no |
| suppressed | Overlaps another recommendation, or safe mode is on | no |

**Policies:**
- **Right-sizing:** CPU p95 < 40% and memory p95 < 50% (or unknown, which means review),
  observed for ≥ 14 days. Savings = 50% of the reference monthly cost (one size step).
  The plausibility cap is 90%.
- **Idle, compute:** CPU p95 ≤ 3% and a secondary signal (requests ≤ 5/day or network
  ≤ 5 MB/day), observed for ≥ 7 days.
- **Idle, disks/IPs/LBs:** `attached=false`. Savings = 100% of the reference cost.
- **Commitments:** ≥ 30 days of history. Coverage = 80% of the P10 daily cost minus savings
  already claimed by other recommendations. Uses the ASSUMED discount per provider (AWS 28%,
  GCP 25%, Azure 30%). CV > 0.35, or effective cost more than 10% below billed, means
  review.

**Reference cost:**
- Per resource: mean daily billed cost over the trailing 30 days with data, × 30.
- Overall: total over the trailing 30 days ÷ days with data × 30.
