"""NT-3 Unsafe recommendation (mandatory negative test).

Cases: missing utilisation, unsupported resource type, too-short observation
window, implausible savings, protected resource, no billing evidence, CPU-only
"idle", insufficient commitment history, and safe mode.

Expected: none of them yields an ``actionable`` recommendation with a savings
claim; each is ``rejected``, ``insufficient_data``, ``requires_review`` or
``suppressed`` with an explicit reason, and nothing is ever auto-applied.
"""

import pytest

from finops_engine.engine_config import EngineConfig
from finops_engine.ingestion import ingestion_store
from finops_engine.optimization import UtilizationSample
from finops_engine.optimization.engine import generate_recommendations
from tests.conftest import FIXTURES, ingest_file

RID = "arn:aws:ec2:us-east-1:111111111111:instance/i-0web01"


@pytest.fixture
def records():
    ingest_file(FIXTURES / "billing_benchmark_v1.csv")
    return ingestion_store.snapshot_records()


def _one(records, sample, cfg=None, kind="rightsizing"):
    recs = generate_recommendations(records, [sample], cfg or EngineConfig())["recommendations"]
    return next(r for r in recs if r["kind"] == kind)


def test_missing_utilization_is_insufficient_data(records):
    rec = _one(records, UtilizationSample(resource_id=RID, resource_type="vm", observation_days=30))
    assert rec["status"] == "insufficient_data"
    assert rec["estimated_monthly_savings_usd"] is None
    assert "UNKNOWN" in rec["reason"]


def test_unsupported_resource_type_rejected(records):
    rec = _one(records, UtilizationSample(resource_id=RID, resource_type="other", cpu_p95_pct=5, observation_days=30))
    assert rec["status"] == "rejected"
    assert rec["estimated_monthly_savings_usd"] is None


def test_short_observation_window_requires_review(records):
    rec = _one(
        records,
        UtilizationSample(resource_id=RID, resource_type="vm", cpu_p95_pct=10, mem_p95_pct=10, observation_days=3),
    )
    assert rec["status"] == "requires_review"
    assert "observation window" in rec["reason"]


def test_implausible_savings_rejected(records):
    cfg = EngineConfig.model_validate({"rightsizing": {"one_step_savings_fraction": 0.95}})
    rec = _one(
        records,
        UtilizationSample(resource_id=RID, resource_type="vm", cpu_p95_pct=10, mem_p95_pct=10, observation_days=30),
        cfg,
    )
    assert rec["status"] == "rejected"
    assert "implausible" in rec["reason"]
    assert rec["estimated_monthly_savings_usd"] is None


def test_protected_resource_requires_review(records):
    sample = UtilizationSample(
        resource_id=RID,
        resource_type="vm",
        cpu_p95_pct=10,
        mem_p95_pct=10,
        observation_days=30,
        tags={"do-not-delete": "true"},
    )
    assert _one(records, sample)["status"] == "requires_review"


def test_no_billing_evidence_requires_review_without_savings(records):
    sample = UtilizationSample(
        resource_id="i-not-in-billing", resource_type="vm", cpu_p95_pct=10, mem_p95_pct=10, observation_days=30
    )
    rec = _one(records, sample)
    assert rec["status"] == "requires_review"
    assert rec["estimated_monthly_savings_usd"] is None


def test_cpu_only_idle_signal_is_not_actionable(records):
    sample = UtilizationSample(resource_id=RID, resource_type="vm", cpu_p95_pct=0.5, mem_p95_pct=5, observation_days=30)
    rec = _one(records, sample, kind="idle")
    assert rec["status"] == "requires_review"
    assert "CPU alone is insufficient" in rec["reason"]


def test_disk_with_unknown_attachment_is_insufficient(records):
    sample = UtilizationSample(resource_id=RID, resource_type="disk", observation_days=30)
    rec = _one(records, sample, kind="idle")
    assert rec["status"] == "insufficient_data"


def test_safe_mode_suppresses_everything(records):
    sample = UtilizationSample(resource_id=RID, resource_type="vm", cpu_p95_pct=10, mem_p95_pct=10, observation_days=30)
    result = generate_recommendations(records, [sample], EngineConfig(), safe_mode=True, safe_mode_reason="test")
    assert result["recommendations"]
    assert {r["status"] for r in result["recommendations"]} == {"suppressed"}
    assert result["summary"]["estimated_monthly_savings_usd"] == 0


def test_invalid_utilization_values_rejected_at_the_boundary():
    with pytest.raises(ValueError):
        UtilizationSample(resource_id="x", cpu_p95_pct=140)
    with pytest.raises(ValueError):
        UtilizationSample(resource_id="x", observation_days=-1)


def test_no_recommendation_is_ever_auto_applied(records):
    samples = [
        UtilizationSample(resource_id=RID, resource_type="vm", cpu_p95_pct=10, mem_p95_pct=10, observation_days=30)
    ]
    for rec in generate_recommendations(records, samples, EngineConfig())["recommendations"]:
        assert rec["auto_apply"] is False
        assert rec["safe_next_action"]
        assert not any(word in rec["action"] for word in ("delete_now", "terminate_now", "apply"))
