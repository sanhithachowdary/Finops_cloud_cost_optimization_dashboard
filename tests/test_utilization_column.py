"""Deriving utilisation samples from a billing CSV's per-row utilisation column."""

import pytest

from finops_engine.engine_config import EngineConfig
from finops_engine.ingestion.utilization_column import infer_resource_type, samples_from_billing_rows
from finops_engine.optimization.engine import generate_recommendations


def _rows(resource, service, utils, provider="AWS", column="ResourceUtilizationPercent"):
    return [
        {
            "ChargePeriodStart": f"2026-04-{d + 1:02d}T00:00:00Z",
            "ProviderName": provider,
            "ServiceName": service,
            "ResourceId": resource,
            column: u,
        }
        for d, u in enumerate(utils)
    ]


@pytest.mark.parametrize(
    "service,expected",
    [
        ("AmazonEC2", "vm"),
        ("Virtual Machines", "vm"),
        ("GCP_Compute", "vm"),
        ("AmazonRDS", "database"),
        ("Azure SQL", "database"),
        ("AmazonS3", None),
        ("AWS_Storage", None),
        ("AWSLambda", None),
        ("Azure Functions", None),
        ("AWS_Serverless", None),
    ],
)
def test_resource_type_inference(service, expected):
    assert infer_resource_type(service) == expected


def test_samples_are_cpu_proxies_with_memory_unknown():
    rows = _rows("i-1", "AmazonEC2", [10 + d for d in range(20)])
    samples, skipped = samples_from_billing_rows(rows)
    assert skipped == []
    (s,) = samples
    assert s.resource_type == "vm" and s.provider == "AWS"
    assert s.cpu_avg_pct == pytest.approx(19.5)
    assert s.cpu_p95_pct == 28  # nearest-rank p95 of 10..29
    assert s.observation_days == 20
    assert s.mem_p95_pct is None and s.percentiles_are_proxies is True
    assert s.source.startswith("billing_column:")


def test_storage_and_serverless_are_skipped_with_reason():
    rows = _rows("b-1", "AmazonS3", [100] * 5) + _rows("f-1", "AWSLambda", [5] * 5)
    samples, skipped = samples_from_billing_rows(rows)
    assert samples == []
    assert {s["resource_id"] for s in skipped} == {"b-1", "f-1"}


def test_invalid_values_are_ignored_and_recorded():
    rows = _rows("i-2", "AmazonEC2", [20, "abc", 150, -1, "NULL", 30])
    (s,), _ = samples_from_billing_rows(rows)
    assert s.cpu_avg_pct == 25 and s.observation_days == 2
    assert "invalid values ignored" in s.source


def test_low_utilisation_from_column_gives_review_not_actionable_rightsizing():
    from datetime import datetime, timedelta, timezone

    from finops_engine.schema import CloudProvider, FocusRecord

    start = datetime(2026, 4, 1, tzinfo=timezone.utc)
    records = [
        FocusRecord(
            provider_name=CloudProvider.AWS,
            billed_cost=10.0,
            effective_cost=10.0,
            service_name="AmazonEC2",
            resource_id="i-low",
            billing_period_start=start + timedelta(days=d),
            billing_period_end=start + timedelta(days=d + 1),
        )
        for d in range(30)
    ]
    samples, _ = samples_from_billing_rows(_rows("i-low", "AmazonEC2", [12.0] * 30))
    recs = generate_recommendations(records, samples, EngineConfig())["recommendations"]
    rs = next(r for r in recs if r["kind"] == "rightsizing")
    assert rs["status"] == "requires_review"  # memory unknown + proxy percentiles
    assert rs["estimated_monthly_savings_usd"] is not None
