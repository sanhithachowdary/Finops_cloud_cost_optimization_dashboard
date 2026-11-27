# Negative-Test Results (NT-1 … NT-5)

**Command:** `pytest tests/negative -v -p no:cacheprovider`
**Environment:** Linux 2-vCPU workspace, CPython 3.13.16, 2026-10-12. FastAPI, pydantic-settings,
cachetools and prometheus-client were built from the official sources at the pinned tags.
**Result:** **38 passed, 0 failed** (`negative-tests-output.txt`).

The timing-sensitive modules (NT-1 to NT-5 plus ingestion concurrency, 49 tests) were run
5 times in a row. All 5 runs passed.

| ID | What is injected | Expected | Actual | Pass | Evidence |
|---|---|---|---|---|---|
| NT-1 Billing complexity | `nt1_problematic_billing.csv`, 17 rows: exact duplicate, missing currency, EUR without FX rate, `abc` and `NaN` costs, bad date, end before start, negative usage cost, 5e9 cost, out-of-order row, `seconds` and `hrs` units, naive timestamps, conflicting duplicate pair, unknown provider, year-2099 row. Also bad JSON, empty body, unsupported schema version and an oversized upload via the API. | Each problem is detected with a reason. Bad rows are quarantined and kept as evidence. The duplicate is counted once. Totals reconcile. Safe mode suppresses recommendations. The API returns 422/413 with no stack trace. | 12 rows quarantined with the expected reason codes. 1 duplicate. 4 accepted. Units converted and recorded. 2 naive-timestamp warnings. Reconciliation holds: accepted $137 + quarantined + duplicate $100. 2 unparseable-cost rows reported. Quarantine ratio 0.71 → safe mode → all recommendations `suppressed`. | ✅ 6/6 | `tests/negative/test_nt1_billing_complexity.py`; batch evidence JSON in `$FINOP_EVIDENCE_DIR/ingestion/` |
| NT-2 Attribution | `nt2_attribution.csv`: missing ResourceId, account `unknown`, `i-shared` under 2 accounts. Conflict across batches. OpenCost `__idle__` with and without a provider. | Totals preserved. Explicit unattributed bucket. Conflicts flagged. Recommendations on conflicting resources need review. Rows with no determinable provider are quarantined. | $240 total preserved. Unattributed resource $25 and account $15 shown explicitly. `i-shared` flagged in report and breakdown. Right-sizing on it → `requires_review`. Cross-batch conflict detected. OpenCost idle $10 → `(unattributed)` workload; without a provider → quarantined `missing_provider_name`. | ✅ 6/6 | `test_nt2_attribution.py` |
| NT-3 Unsafe recommendation | Missing utilisation; unsupported type; 3-day window; implausible savings (fraction 0.95 > cap 0.9); `do-not-delete` tag; no billing evidence; CPU-only idle; disk with unknown attachment; safe mode; out-of-range utilisation. | Never `actionable` with a savings claim. Each is rejected, insufficient_data, requires_review or suppressed with a reason. Never auto-applied. | All cases produced the expected status. `auto_apply` is false everywhere. Invalid utilisation was rejected at the model boundary. | ✅ 11/11 | `test_nt3_unsafe_recommendation.py` |
| NT-4 Latency / overload | Connector sleeping 1.5–2 s against a 0.2 s timeout. Erroring connector. Partial provider outage. 8 blocked workers (saturation). Bounded retry count. Open breaker. API under a hanging dependency. | Bounded time and attempts, then fail fast, then recover. Saturation rejected immediately. Partial failure labelled. API returns 503 quickly. | Slow call bounded to under 1 s (2 attempts). Breaker opens after 2 failures; the next call fails in under 0.1 s. Half-open probe after 0.35 s succeeds and a `recovered` event is recorded with its duration. Saturation rejected in under 50 ms. Exactly 1+retries attempts. API 503 in under 1.5 s and the operator guidance names the connector. | ✅ 8/8 | `test_nt4_latency_overload.py` |
| NT-5 Insufficient telemetry | Metrics refresh raises. Stale signal. Component never reported. Live mode with no utilisation source. Corrupt utilisation samples. Too few SLO samples. Unhandled exception. | Report failed, unknown or insufficient. Never claim success. Never leak internals. | Refresh → `failed` and Prometheus `finops_component_status{…failed} 1`. Stale → `unknown`. No signal → `unknown`. Live → "NOT evaluated" note and no right-sizing/idle results. 3 of 4 corrupt samples rejected with reasons. SLO → `insufficient_evidence`. 500 returns only an `incident_id`. | ✅ 7/7 | `test_nt5_insufficient_telemetry.py` |

Other edge cases covered elsewhere:
- **Empty datasets:** `test_spend_analytics.py`, `test_evaluation.py`
- **Large, zero and credit values:** `test_ingestion.py`
- **Timezone boundary:** `test_spend_analytics.py::test_timezone_boundary_row_lands_on_utc_day`
- **Idempotent and concurrent ingestion:** `test_ingestion.py`
- **API validation:** `test_api_extended.py::test_query_validation`
- **Auth boundary:** `test_api_extended.py::test_auth_boundary_on_new_endpoints`, `test_api.py`
