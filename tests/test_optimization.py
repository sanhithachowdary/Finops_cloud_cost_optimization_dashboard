"""FR-4/5/6: right-sizing, idle detection, commitments and savings accounting."""

import json

import pytest

from finops_engine.engine_config import EngineConfig
from finops_engine.ingestion import ingestion_store
from finops_engine.optimization import UtilizationSample
from finops_engine.optimization.engine import generate_recommendations, realized_savings
from tests.conftest import FIXTURES, ingest_file


@pytest.fixture
def benchmark():
    ingest_file(FIXTURES / "billing_benchmark_v1.csv")
    payload = json.loads((FIXTURES / "utilization_benchmark_v1.json").read_text())
    samples = [UtilizationSample(**{k: v for k, v in s.items() if k != "expected"}) for s in payload["samples"]]
    expected = {s["resource_id"]: s["expected"] for s in payload["samples"]}
    return ingestion_store.snapshot_records(), samples, expected


def test_recommendation_outcomes_match_hand_derived_oracle(benchmark):
    records, samples, expected = benchmark
    result = generate_recommendations(records, samples, EngineConfig())
    got: dict[str, dict[str, str]] = {}
    for rec in result["recommendations"]:
        if rec["resource_id"]:
            got.setdefault(rec["resource_id"], {})[rec["kind"]] = rec["status"]
    for resource_id, exp in expected.items():
        for kind, status in exp.items():
            assert got.get(resource_id, {}).get(kind) == status, (resource_id, kind, got.get(resource_id))


def test_every_recommendation_has_required_contract(benchmark):
    records, samples, _ = benchmark
    for rec in generate_recommendations(records, samples, EngineConfig())["recommendations"]:
        assert rec["auto_apply"] is False
        assert rec["reason"] and rec["safe_next_action"] and rec["confidence"]
        assert isinstance(rec["assumptions"], list) and isinstance(rec["limitations"], list)
        assert rec["config_version"] == EngineConfig().version
        if rec["status"] == "actionable":
            assert rec["estimated_monthly_savings_usd"] and rec["estimated_monthly_savings_usd"] > 0
        if rec["status"] in ("insufficient_data", "rejected"):
            assert rec["estimated_monthly_savings_usd"] is None


def test_savings_summary_separates_estimated_potential_and_realized(benchmark):
    records, samples, _ = benchmark
    result = generate_recommendations(records, samples, EngineConfig())
    summary = result["summary"]
    actionable = sum(
        r["estimated_monthly_savings_usd"] for r in result["recommendations"] if r["status"] == "actionable"
    )
    review = sum(
        r["estimated_monthly_savings_usd"] or 0 for r in result["recommendations"] if r["status"] == "requires_review"
    )
    assert summary["estimated_monthly_savings_usd"] == pytest.approx(actionable, abs=0.05)
    assert summary["potential_monthly_savings_requires_review_usd"] == pytest.approx(review, abs=0.05)
    assert summary["realized_savings"]["status"] == "not_measured"
    ref = summary["reference"]["monthly_cost_usd"]
    assert summary["estimated_relative_savings_pct"] == pytest.approx(100 * actionable / ref, abs=0.01)


def test_idle_supersedes_rightsizing_no_double_count(benchmark):
    records, samples, _ = benchmark
    recs = generate_recommendations(records, samples, EngineConfig())["recommendations"]
    legacy = [r for r in recs if r["resource_id"] and r["resource_id"].endswith("i-0legacy07")]
    statuses = {r["kind"]: r["status"] for r in legacy}
    assert statuses == {"idle": "actionable", "rightsizing": "suppressed"}


def test_commitment_baseline_reduced_by_claimed_savings(benchmark):
    records, samples, _ = benchmark
    with_claims = generate_recommendations(records, samples, EngineConfig())["recommendations"]
    without = generate_recommendations(records, [], EngineConfig())["recommendations"]

    def ec2(recs):
        return next(r for r in recs if r["kind"] == "commitment" and r["service"] == "AmazonEC2")

    assert ec2(with_claims)["estimated_monthly_savings_usd"] < ec2(without)["estimated_monthly_savings_usd"]
    assert ec2(with_claims)["evidence"]["claimed_by_other_recommendations_daily_usd"] > 0


def test_commitments_require_history_and_discount_rate():
    ingest_file(FIXTURES / "billing_small_v1.csv")  # only a few days of data
    recs = generate_recommendations(ingestion_store.snapshot_records(), [], EngineConfig())["recommendations"]
    commitments = [r for r in recs if r["kind"] == "commitment"]
    assert commitments and all(r["status"] == "insufficient_data" for r in commitments)
    assert all(r["estimated_monthly_savings_usd"] is None for r in commitments)

    ingestion_store.clear()
    ingest_file(FIXTURES / "billing_benchmark_v1.csv")
    cfg = EngineConfig.model_validate({"commitments": {"assumed_discount_rate": {"AWS": 0.28}}})
    recs = generate_recommendations(ingestion_store.snapshot_records(), [], cfg)["recommendations"]
    gcp = [r for r in recs if r["kind"] == "commitment" and r["provider"] == "GCP"]
    assert gcp and all(r["status"] == "insufficient_data" for r in gcp)
    assert all("no discount rate configured" in r["reason"] for r in gcp)


def test_realized_savings_measured_only_with_before_and_after_data():
    ingest_file(FIXTURES / "billing_benchmark_v1.csv")
    records = ingestion_store.snapshot_records()
    rid = "arn:aws:ec2:us-east-1:111111111111:instance/i-0web01"
    not_yet = realized_savings(records, {rid}, "2026-06-25", window_days=14)
    assert not_yet["status"] == "not_measured"
    measured = realized_savings(records, {rid}, "2026-06-01", window_days=14)
    assert measured["status"] == "measured"
    assert "monthly_usd" in measured


def test_rightsizing_is_suppressed_when_an_idle_review_exists_for_the_same_resource():
    # Regression: an idle VM needing review was also offered for resizing, double-counting it in
    # "potential" savings.
    from datetime import datetime, timedelta, timezone

    from finops_engine.engine_config import EngineConfig
    from finops_engine.optimization.engine import generate_recommendations
    from finops_engine.optimization.models import UtilizationSample
    from finops_engine.schema import CloudProvider, FocusRecord

    t = datetime(2026, 5, 1, tzinfo=timezone.utc)
    records = [
        FocusRecord(
            provider_name=CloudProvider.AZURE,
            billed_cost=4.6,
            effective_cost=4.6,
            service_name="Virtual Machines",
            resource_id="vm-1",
            billing_period_start=t + timedelta(days=i),
            billing_period_end=t + timedelta(days=i + 1),
        )
        for i in range(30)
    ]
    sample = UtilizationSample(resource_id="vm-1", provider="Azure", cpu_p95_pct=2.0, observation_days=30)
    out = generate_recommendations(records, [sample], EngineConfig())
    by_kind = {r["kind"]: r for r in out["recommendations"] if r["resource_id"] == "vm-1"}
    assert by_kind["idle"]["status"] == "requires_review"
    assert by_kind["rightsizing"]["status"] == "suppressed"
    assert out["summary"]["potential_monthly_savings_requires_review_usd"] == pytest.approx(
        by_kind["idle"]["estimated_monthly_savings_usd"]
    )
