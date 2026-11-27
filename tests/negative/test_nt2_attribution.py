"""NT-2 Attribution accuracy (mandatory negative test).

Input: ``nt2_attribution.csv`` (missing resource id, placeholder account, one
resource billed under two different accounts) plus an OpenCost payload with
an ``__idle__`` (unattributable) allocation.

Expected: totals are preserved (nothing dropped or reassigned), unattributed
spend is reported in an explicit bucket, conflicting resources are flagged in
analytics and the ingestion report, and recommendations touching them are
downgraded to ``requires_review``.
"""

import json

import pytest

from finops_engine.analytics import spend_breakdown, spend_overview
from finops_engine.engine_config import EngineConfig
from finops_engine.ingestion import ingestion_store, opencost_to_rows
from finops_engine.optimization import UtilizationSample
from finops_engine.optimization.engine import generate_recommendations
from tests.conftest import FIXTURES, ingest_file


@pytest.fixture
def report():
    return ingest_file(FIXTURES / "negative" / "nt2_attribution.csv")


def test_totals_preserved_and_unattributed_bucket_explicit(report):
    records = ingestion_store.snapshot_records()
    assert spend_overview(records)["total_cost_usd"] == pytest.approx(240.0)
    by_resource = spend_breakdown(records, "resource")
    assert by_resource["unattributed_cost_usd"] == pytest.approx(25.0)
    by_account = spend_breakdown(records, "account")
    assert by_account["unattributed_cost_usd"] == pytest.approx(15.0)  # account "unknown"
    assert sum(i["cost_usd"] for i in by_account["items"]) == pytest.approx(240.0)


def test_conflicting_attribution_detected_and_flagged(report):
    assert report["attribution"]["conflicting_resource_ids"] == ["i-shared"]
    assert report["attribution"]["attributed_cost_share"] == pytest.approx((240 - 25 - 15) / 240, abs=1e-4)
    items = spend_breakdown(
        ingestion_store.snapshot_records(),
        "resource",
        conflicting_resource_ids=ingestion_store.conflicting_resource_ids,
    )["items"]
    shared = next(i for i in items if i["key"] == "i-shared")
    assert "conflicting_attribution" in shared["flags"]


def test_recommendation_on_conflicting_resource_requires_review(report):
    sample = UtilizationSample(
        resource_id="i-shared",
        provider="AWS",
        resource_type="vm",
        cpu_p95_pct=10,
        mem_p95_pct=20,
        observation_days=30,
    )
    recs = generate_recommendations(
        ingestion_store.snapshot_records(),
        [sample],
        EngineConfig(),
        conflicting_resource_ids=ingestion_store.conflicting_resource_ids,
    )["recommendations"]
    rs = next(r for r in recs if r["kind"] == "rightsizing")
    assert rs["status"] == "requires_review"
    assert "conflicting billing attribution" in rs["reason"]


def test_conflict_detected_across_batches():
    ingest_file(FIXTURES / "billing_small_v1.csv")  # i-1 under account 111111111111
    rows = [
        {
            "ProviderName": "AWS",
            "BilledCost": "1",
            "BillingCurrency": "USD",
            "ServiceName": "AmazonEC2",
            "SubAccountId": "999",
            "ResourceId": "i-1",
            "ChargePeriodStart": "2026-06-05T00:00:00Z",
            "ChargePeriodEnd": "2026-06-06T00:00:00Z",
        }
    ]
    rep = ingestion_store.ingest_rows(rows, raw_bytes=b"later", source_name="l", source_type="t", schema_version="1.0")
    assert rep["attribution"]["conflicting_resource_ids"] == ["i-1"]


def test_opencost_row_without_determinable_provider_is_quarantined():
    payload = json.loads((FIXTURES / "opencost_allocation_v1.json").read_text())
    rows, samples = opencost_to_rows(payload)  # __idle__ has no providerID and no default given
    rep = ingestion_store.ingest_rows(
        rows, raw_bytes=b"oc-no-provider", source_name="oc", source_type="opencost", schema_version="1.0"
    )
    assert rep["rows_quarantined"] == 1
    assert rep["quarantine_reasons"] == {"missing_provider_name": 1}


def test_opencost_idle_allocation_is_unattributed_workload():
    payload = json.loads((FIXTURES / "opencost_allocation_v1.json").read_text())
    rows, samples = opencost_to_rows(payload, default_provider="AWS")
    raw = json.dumps(payload).encode()
    ingestion_store.ingest_rows(
        rows, raw_bytes=raw, source_name="oc", source_type="opencost", schema_version="1.0", utilization=samples
    )
    by_workload = spend_breakdown(ingestion_store.snapshot_records(), "workload")
    assert by_workload["unattributed_cost_usd"] == pytest.approx(10.0)
    assert by_workload["total_cost_usd"] == pytest.approx(64.0)
