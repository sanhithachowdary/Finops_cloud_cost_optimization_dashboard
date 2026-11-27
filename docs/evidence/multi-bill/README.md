# Multiple bills uploaded together (2026-10-24)

**Data label:** GENERATED bills from `scripts/period_bills.py` (seed 2026, events preset `demo`).
Not real cloud bills.

Produced by `python -m scripts.multi_bill_check --out docs/evidence/multi-bill/report.json`, which
runs the real API in-process with the shipped INR config (`2026.10-v2-inr`) and uploads:

1. 1 Jan – 31 Mar 2026, priced in USD
2. 1 Mar – 30 Jun 2026, priced in INR (overlaps March)
3. 1 Jul – 30 Sep 2026, priced in USD
4. bill 1 again

| Check | Result |
|---|---|
| All bills accepted, nothing quarantined | pass |
| March overlap counted once (527 duplicate lines, including USD/INR rounding) | pass |
| Re-uploading bill 1 is a no-op (idempotent replay) | pass |
| Total spend = independent pandas sum over distinct line Ids | pass (exact) |
| Spend per provider = independent sum | pass |
| Every planted spike/step detected (3 of 3) | pass |

Anomaly precision on these bills is 0.75 (3 of 4 alerts are planted events; the extra alert,
12 May AWS Lambda, is a high day of ordinary random variation). Forecast back-test MAPE 1.43 %.
Recommendations: 12 commitment suggestions (actionable); right-sizing for the under-used batch
VM and an idle-VM finding both need review (memory and network telemetry are not in the bills).

The test `tests/test_multi_bill.py` checks the same properties on every test run, plus that a
later bill restating an earlier line with a different amount is quarantined
(`conflicting_duplicate_across_batches`) instead of being added on top.
