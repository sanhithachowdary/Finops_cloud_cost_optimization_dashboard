"""FR-1 ingestion: validation, provenance, idempotency, OpenCost adapter."""

import json
import threading

import pytest

from finops_engine.engine_config import EngineConfig, IngestionConfig
from finops_engine.ingestion import BatchRejectedError, ingestion_store, opencost_to_rows, validate_rows
from tests.conftest import FIXTURES, ingest_file


def _row(**over):
    row = {
        "ProviderName": "AWS",
        "ChargeCategory": "Usage",
        "BilledCost": "10.00",
        "BillingCurrency": "USD",
        "ServiceName": "AmazonEC2",
        "SubAccountId": "111",
        "ResourceId": "i-1",
        "ChargePeriodStart": "2026-06-01T00:00:00Z",
        "ChargePeriodEnd": "2026-06-02T00:00:00Z",
    }
    row.update(over)
    return row


def test_valid_batch_has_provenance_and_reconciles():
    report = ingest_file(FIXTURES / "billing_small_v1.csv")
    assert report["status"] == "accepted"
    assert report["rows_received"] == report["rows_accepted"] == 9
    assert len(report["input_sha256"]) == 64
    assert report["config_version"] == EngineConfig().version
    assert report["schema_version"] == "1.0"
    assert report["reconciliation"]["parsed_input_cost_usd"] == pytest.approx(425.50)
    assert report["transformations"]["effective_cost_defaulted_to_billed"] == 6


def test_reingesting_same_file_is_idempotent():
    first = ingest_file(FIXTURES / "billing_small_v1.csv")
    second = ingest_file(FIXTURES / "billing_small_v1.csv")
    assert second["idempotent_replay"] is True
    assert second["batch_id"] == first["batch_id"]
    assert len(ingestion_store.snapshot_records()) == 9


def test_same_rows_in_different_file_are_not_double_counted():
    rows = [_row(), _row(ResourceId="i-2")]
    cfg = IngestionConfig()
    ingestion_store.ingest_rows(rows, raw_bytes=b"a", source_name="a", source_type="t", schema_version="1.0")
    report = ingestion_store.ingest_rows(
        rows + [_row(ResourceId="i-3")], raw_bytes=b"b", source_name="b", source_type="t", schema_version="1.0"
    )
    assert report["rows_duplicate"] == 2
    assert report["rows_accepted"] == 1
    assert len(ingestion_store.snapshot_records()) == 3
    assert cfg.base_currency == "USD"


def test_unsupported_schema_version_rejects_whole_batch():
    with pytest.raises(BatchRejectedError, match="unsupported schema version"):
        ingestion_store.ingest_rows([_row()], raw_bytes=b"x", source_name="x", source_type="t", schema_version="0.5")
    assert ingestion_store.snapshot_records() == []


def test_fx_conversion_only_with_configured_rate():
    cfg = IngestionConfig(fx_rates_to_base={"EUR": 1.10})
    result = validate_rows([_row(BillingCurrency="EUR", BilledCost="100")], cfg)
    assert result.accepted[0].billed_cost == pytest.approx(110.0)
    assert result.accepted[0].currency == "USD"
    assert result.transformations["fx_EUR_to_USD"] == 1


def test_credit_rows_allowed_negative_but_positive_credit_quarantined():
    cfg = IngestionConfig()
    ok = validate_rows([_row(ChargeCategory="Credit", BilledCost="-5")], cfg)
    assert len(ok.accepted) == 1
    bad = validate_rows([_row(ChargeCategory="Credit", BilledCost="5")], cfg)
    assert bad.quarantined[0].reasons == ["positive_credit"]


def test_large_and_zero_values():
    cfg = IngestionConfig()
    result = validate_rows([_row(BilledCost="0"), _row(BilledCost="999999.99", ResourceId="i-big")], cfg)
    assert len(result.accepted) == 2


def test_concurrent_ingestion_of_same_file_creates_one_batch():
    raw = (FIXTURES / "billing_small_v1.csv").read_bytes()
    errors = []

    def worker():
        try:
            ingest_file(FIXTURES / "billing_small_v1.csv")
        except Exception as exc:  # pragma: no cover - would fail the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert len(ingestion_store.batches) == 1
    assert len(ingestion_store.snapshot_records()) == 9
    assert raw


def test_evidence_file_written(tmp_path, monkeypatch):
    monkeypatch.setenv("FINOP_EVIDENCE_DIR", str(tmp_path))
    report = ingest_file(FIXTURES / "negative" / "nt1_problematic_billing.csv")
    evidence = json.loads((tmp_path / "ingestion" / f"batch-{report['batch_id']}.json").read_text())
    assert evidence["report"]["rows_quarantined"] == len(evidence["quarantined_rows"])
    assert all(q["reasons"] for q in evidence["quarantined_rows"])


def test_opencost_adapter_maps_workloads_and_utilization():
    payload = json.loads((FIXTURES / "opencost_allocation_v1.json").read_text())
    rows, samples = opencost_to_rows(payload)
    assert len(rows) == 3
    checkout = next(r for r in rows if "checkout" in (r["resource_id"] or ""))
    assert checkout["provider_name"] == "AWS"  # from providerID aws://
    assert checkout["billed_cost"] == 18.0
    assert checkout["tags"]["k8s_namespace"] == "checkout"
    idle = next(r for r in rows if r["resource_id"] is None)
    assert idle["tags"]["attribution"] == "unattributed:__idle__"
    by_id = {s.resource_id: s for s in samples}
    s = by_id["k8s://prod-eks/checkout/deployment/checkout-api"]
    assert s.cpu_p95_pct == pytest.approx(15.0)  # 0.6 / 4.0
    assert s.mem_p95_pct == pytest.approx(25.0)
    assert s.percentiles_are_proxies is True
    assert s.observation_days == pytest.approx(30.0)


def test_opencost_payload_errors():
    with pytest.raises(ValueError):
        opencost_to_rows({"code": 500, "data": []})
    with pytest.raises(ValueError):
        opencost_to_rows(["not", "an", "object"])


# --- Regressions found with the FinOps Foundation public FOCUS 1.0 sample (2026-10-16) ---


def test_literal_null_strings_are_missing_values():
    result = validate_rows([_row(ResourceId="NULL", EffectiveCost="NULL", ConsumedQuantity="null")], IngestionConfig())
    rec = result.accepted[0]
    assert rec.resource_id is None  # unattributed, not a resource literally called "NULL"
    assert rec.effective_cost == rec.billed_cost
    assert result.transformations["effective_cost_defaulted_to_billed"] == 1


def test_distinct_line_items_in_same_hour_are_not_duplicates():
    base = {"ChargeDescription": "data transfer in", "SkuPriceId": "SKU-1"}
    rows = [
        _row(Id="1001", **base),
        _row(Id="1002", **base),  # same amount, different line-item id -> separate charge
        _row(BilledCost="12.00", SkuPriceId="SKU-2", ChargeDescription="EBS"),  # different SKU
    ]
    result = validate_rows(rows, IngestionConfig())
    assert len(result.accepted) == 3
    assert not result.duplicate_rows and not result.quarantined


def test_same_line_item_id_is_still_deduplicated():
    result = validate_rows([_row(Id="7"), _row(Id="7")], IngestionConfig())
    assert len(result.accepted) == 1 and result.duplicate_rows == [2]


def test_reversal_row_accepted_but_inconsistent_sign_quarantined():
    rows = [
        _row(BilledCost="-0.50", ConsumedQuantity="-2", ResourceId="r-rev"),  # reversal: both negative
        _row(BilledCost="0.50", ConsumedQuantity="-2", ResourceId="r-bad"),  # inconsistent
        _row(BilledCost="-0.50", ConsumedQuantity="2", ResourceId="r-bad2"),  # inconsistent
    ]
    result = validate_rows(rows, IngestionConfig())
    assert [r.resource_id for r in result.accepted] == ["r-rev"]
    assert result.warnings["usage_reversal_row_accepted"] == 1
    reasons = {q.row_number: q.reasons for q in result.quarantined}
    assert reasons == {2: ["negative_usage_quantity"], 3: ["negative_cost_for_non_credit"]}


def test_configured_usage_units_are_accepted_without_warning():
    from finops_engine.engine_config import IngestionConfig
    from finops_engine.ingestion import validate_rows

    def row(i, unit):
        return {
            "ProviderName": "AWS",
            "ServiceName": "AWSLambda",
            "BilledCost": "1",
            "BillingCurrency": "USD",
            "ChargePeriodStart": f"2026-06-{i:02d}T00:00:00Z",
            "ChargePeriodEnd": f"2026-06-{i:02d}T23:59:59Z",
            "ConsumedQuantity": "10",
            "UsageUnit": unit,
        }

    res = validate_rows([row(1, "GB-Seconds"), row(2, "invocations"), row(3, "GB-Month")], IngestionConfig())
    assert dict(res.warnings).get("unrecognised_usage_unit", 0) == 0
    assert sorted(r.usage_unit for r in res.accepted) == ["GB-Months", "GB-Seconds", "Invocations"]
    res = validate_rows([row(4, "furlongs")], IngestionConfig())
    assert res.warnings["unrecognised_usage_unit"] == 1
