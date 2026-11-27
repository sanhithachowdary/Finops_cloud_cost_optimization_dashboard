# Limitations and Assumptions

## Data and measurement

- **All results come from SYNTHETIC fixtures or the mock generator.** They show that the
  logic is correct, not what savings or accuracy you will see on real bills.
- **No Q2 baseline, sandbox, workload trace or hardware envelope was supplied.**
  - KPI-1, KPI-5 and KPI-6 relative targets are blocked, and so is AC-1.
  - KPI-6 is compared against the pre-change code on the same machine. That is a proxy,
    not the Q2 reference.
- **Realised savings are not measured.** Nothing was implemented, and there is no
  post-change billing. `realized_savings()` exists, but it attributes the whole
  before/after difference to the change; confounders are not removed.
- **Latency is measured in-process** (TestClient), from one run on a shared 2-vCPU host.
  Endpoint-level differences of tens of milliseconds are within noise. The KPI-6
  improvement comes only from caching repeated anomaly requests; a cold anomaly call still
  takes about 1.5 s.
- **Detector threshold selection:** the 20% materiality gate was chosen after inspecting
  development-set false positives. The independent hold-out (precision 1.00, recall 0.78)
  is the less biased estimate, but the sample is small: 9 labelled anomalies.

## Functional limits

- **Live connectors (AWS/GCP/Azure)** are unchanged from the original and were not run
  against real accounts. Their unit tests need the cloud SDKs and were skipped here.
- **OpenCost/Kubecost:** only exported `/allocation` JSON is parsed, and only fixtures were
  tested. There is no live API client. OpenCost exposes averages, not percentiles, so the
  average is used as a p95 proxy and confidence is reduced. Rows with no determinable
  provider are quarantined unless you pass `?provider=`.
- **Providers:** only AWS, GCP and Azure (including the "Microsoft" alias) are supported.
  Other FOCUS providers, such as the 68 Oracle rows in the public sample, are quarantined as
  `unknown_provider`, never silently dropped.
- **Line-item identity:** exports without an `Id`/SKU/description column can still merge two
  genuinely separate charges that are identical in every other field. They are counted once
  as a duplicate, which is visible in the report.
- **Reporting currency** is INR, converted from USD at a fixed configured rate (₹94.95 per
  $1, market rate on 24 Oct 2026). Rupee amounts move with that one number, not with the
  daily exchange rate. Tests and the KPI harness run in USD.
- **Currency (original note):** the engine's built-in default base currency is USD. Other currencies are converted only with
  operator-supplied FX rates; otherwise they are quarantined. No live FX feed exists.
- **Commitment savings** use ASSUMED discount rates (AWS 28%, GCP 25%, Azure 30%). These
  are not price quotes. Existing coverage is inferred heuristically: effective cost more
  than 10% below billed.
- **Right-sizing** assumes one size step saves 50% (linear price scaling within a family).
  It does not model licensing, burstable credits or reserved capacity already held.
- **The idle heuristic** can flag scheduled or batch workloads between runs. The safe next
  action (stop for a grace period, then delete only after confirmation) mitigates this.
- **The anomaly detector** works on daily (provider, service) totals. The second day of a
  multi-day spike can be missed because the rolling baseline absorbs the first day. Spikes
  under 20%, or under $5/day above baseline (`min_absolute_excess_usd`), are not alerted.
  Lower the floor for very small bills: a cost doubling from $3.60 to $7.20/day is
  suppressed by the default $5 floor. A jump off a perfectly flat baseline is scored against
  a spread floor of 1% of the baseline mean (`evidence.flat_baseline`).
- **Slow drift is not an anomaly.** A cost that grows a little every day (e.g. storage that
  is never cleaned up) raises no alert; it shows in the Trends view and period comparison.
- **The forecast** is a linear trend with no seasonality. On the mock data it did not beat
  the naive baselines (dashboard back-test, `screenshots/light-forecast.png`). It did on
  both benchmark fixtures (`kpi-results.json`).
- **Weekly buckets** are ISO weeks (Monday 00:00 UTC). All bucketing is in UTC; there is
  no per-tenant timezone.

## Operational limits

- **The ingestion store, health registry and caches are in-memory and per-process.** With
  `uvicorn --workers N` (N > 1), each worker has its own store, so the Dockerfile now runs
  **1 worker** (changed from 4 on 2026-10-16). With more workers or K8s replicas, an upload
  lands in one worker only, and health differs per worker. Run a single worker when using
  ingestion, or add persistent storage.
- **Timed-out dependency calls cannot be killed.** Each one holds a worker thread until the
  SDK returns. The pool (8) rejects new work when saturated instead of queueing.
- **Availability SLO** needs external probing and is not measured.
- **Single shared API key**, with no rate limiting.
- **The KEDA autoscaler** (`kubernetes/keda_autoscaler.yaml`) can scale to 15 replicas. Each replica
  has its own in-memory store, so keep 1 replica while using ingestion, until persistent storage exists.

## Assumptions

- **FOCUS-style column names** (or this project's snake_case) are used for exports. Other
  vendor-native formats such as AWS CUR legacy columns need mapping first.
- **Naive timestamps** are UTC (recorded as a warning).
- **Billed cost** is treated as on-demand cost for commitment sizing.
- **Utilisation `null`** means not measured and is never treated as 0%.
- **Utilisation from a billing column** (`scripts/utilization_from_billing.py`) assumes the
  column is CPU %. Memory is unknown, so right-sizing is at best `requires_review`.
  Percentiles are of daily values and are marked as proxies. Resource type is inferred from
  the service name; storage and serverless resources are skipped.
