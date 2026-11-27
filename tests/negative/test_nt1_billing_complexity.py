"""NT-1 Billing-data complexity (mandatory negative test).

Input: ``tests/fixtures/negative/nt1_problematic_billing.csv`` with malformed,
incomplete, duplicated, out-of-order, mixed-unit, mixed-currency, implausible,
conflicting and future-dated rows.

Expected: every problem is detected; bad rows are quarantined with reasons
and retained as evidence; exact duplicates are counted once; totals reconcile
(no silent corruption); a high quarantine ratio puts the engine in safe mode
and suppresses recommendations.
"""

import json

import pytest

from finops_engine.analytics import spend_overview
from finops_engine.engine_config import EngineConfig
from finops_engine.health import health_registry
from finops_engine.ingestion import ingestion_store
from finops_engine.optimization.engine import generate_recommendations
from tests.conftest import FIXTURES, ingest_file

FILE = FIXTURES / "negative" / "nt1_problematic_billing.csv"

EXPECTED_REASONS = {
    3: "missing_currency",
    4: "unsupported_currency",
    5: "malformed_billed_cost",
    6: "malformed_billing_period_start",
    7: "period_end_not_after_start",
    8: "negative_cost_for_non_credit",
    9: "malformed_billed_cost",
    10: "implausible_cost_magnitude",
    14: "conflicting_duplicate",
    15: "conflicting_duplicate",
    16: "unknown_provider",
    17: "timestamp_in_future",
}


@pytest.fixture
def report(tmp_path, monkeypatch):
    monkeypatch.setenv("FINOP_EVIDENCE_DIR", str(tmp_path))
    rep = ingest_file(FILE)
    rep["_evidence"] = json.loads((tmp_path / "ingestion" / f"batch-{rep['batch_id']}.json").read_text())
    return rep


def test_each_problem_row_is_quarantined_with_the_right_reason(report):
    by_row = {q["row_number"]: q["reasons"] for q in report["_evidence"]["quarantined_rows"]}
    for row, reason in EXPECTED_REASONS.items():
        assert reason in by_row.get(row, []), (row, by_row.get(row))
    assert report["rows_quarantined"] == len(EXPECTED_REASONS)


def test_exact_duplicate_counted_once(report):
    assert report["rows_duplicate"] == 1
    ok_rows = [r for r in ingestion_store.snapshot_records() if r.resource_id == "i-ok"]
    assert len(ok_rows) == 1


def test_accepted_rows_and_transformations(report):
    # rows 1, 11, 12, 13 accepted (row 2 duplicate)
    assert report["rows_accepted"] == 4
    assert report["transformations"]["unit_seconds_to_Hours"] == 1
    assert report["transformations"]["unit_synonym_normalised"] == 1
    assert report["warnings"]["naive_timestamp_assumed_utc"] == 2
    assert report["out_of_order_rows"] >= 1
    sec = next(r for r in ingestion_store.snapshot_records() if r.resource_id == "vm-sec")
    assert sec.usage_quantity == pytest.approx(2.0) and sec.usage_unit == "Hours"


def test_totals_reconcile_no_silent_corruption(report):
    recon = report["reconciliation"]
    accepted_total = spend_overview(ingestion_store.snapshot_records())["total_cost_usd"]
    assert accepted_total == pytest.approx(100.0 + 20.0 + 8.0 + 9.0)
    assert recon["accepted_cost_usd"] == pytest.approx(accepted_total)
    assert recon["duplicate_cost_usd"] == pytest.approx(100.0)
    assert recon["parsed_input_cost_usd"] == pytest.approx(
        recon["accepted_cost_usd"] + recon["quarantined_cost_usd"] + recon["duplicate_cost_usd"]
    )
    # 2 rows (abc, NaN) have no parseable cost and are reported as such
    assert report["rows_unparseable_cost"] == 2


def test_high_quarantine_ratio_enters_safe_mode_and_suppresses_recommendations(report):
    assert report["safe_mode_triggered"] is True
    snapshot = health_registry.snapshot()
    assert snapshot["mode"] == "safe"
    result = generate_recommendations(
        ingestion_store.snapshot_records(), [], EngineConfig(), safe_mode=True, safe_mode_reason="NT-1"
    )
    assert all(r["status"] == "suppressed" for r in result["recommendations"])
    assert result["summary"]["estimated_monthly_savings_usd"] == 0


def test_api_rejects_unparseable_and_oversized_payloads(monkeypatch):
    from fastapi.testclient import TestClient

    from finops_engine.api.app import app

    with TestClient(app) as client:
        bad = client.post("/api/v1/ingest/billing?format=json", content=b"{not json")
        assert bad.status_code == 422
        empty = client.post("/api/v1/ingest/billing?format=csv", content=b"")
        assert empty.status_code == 422
        version = client.post("/api/v1/ingest/billing?format=csv&schema_version=0.1", content=FILE.read_bytes())
        assert version.status_code == 422
        too_big = client.post(
            "/api/v1/ingest/billing?format=csv", content=b"x" * (EngineConfig().ingestion.max_upload_bytes + 1)
        )
        assert too_big.status_code == 413
        assert "Traceback" not in bad.text + too_big.text
