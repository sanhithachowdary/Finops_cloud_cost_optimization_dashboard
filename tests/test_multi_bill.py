"""Several bills uploaded one after another (consecutive and overlapping periods, mixed currency)."""

import io
from datetime import date

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from finops_engine.ingestion import ingestion_store
from scripts.period_bills import DEMO_EVENTS, generate_period, parse_period

INR = 94.95


def _csv(df: pd.DataFrame) -> bytes:
    buf = io.StringIO()
    df.to_csv(buf, index=False, lineterminator="\n")
    return buf.getvalue().encode()


@pytest.fixture
def client(monkeypatch, tmp_path):
    import json

    from finops_engine import engine_config
    from finops_engine.api.app import app
    from finops_engine.engine_config import EngineConfig

    cfg = EngineConfig().model_dump(mode="json")
    cfg["ingestion"]["base_currency"] = "INR"
    cfg["ingestion"]["fx_rates_to_base"] = {"USD": INR}
    cfg["anomaly"]["min_absolute_excess_usd"] = 475
    path = tmp_path / "inr.json"
    path.write_text(json.dumps(cfg))
    monkeypatch.setenv("FINOP_ENGINE_CONFIG", str(path))
    engine_config.get_engine_config.cache_clear()
    yield TestClient(app)
    monkeypatch.undo()
    engine_config.get_engine_config.cache_clear()


def test_parse_period():
    assert parse_period("jan-mar", 2026) == (date(2026, 1, 1), date(2026, 3, 31))
    assert parse_period("feb", 2028) == (date(2028, 2, 1), date(2028, 2, 29))
    assert parse_period("nov-feb", 2026) == (date(2026, 11, 1), date(2027, 2, 28))
    with pytest.raises(ValueError):
        parse_period("q1", 2026)


def test_overlapping_bills_contain_identical_rows_for_shared_days():
    a, _ = generate_period(date(2026, 1, 1), date(2026, 3, 31))
    b, _ = generate_period(date(2026, 3, 1), date(2026, 5, 31))
    march_a = a[a["ChargePeriodStart"].str.startswith("2026-03")].reset_index(drop=True)
    march_b = b[b["ChargePeriodStart"].str.startswith("2026-03")].reset_index(drop=True)
    pd.testing.assert_frame_equal(march_a, march_b)


def test_labels_cover_only_events_inside_the_bill():
    _, meta = generate_period(date(2026, 1, 1), date(2026, 3, 31))
    assert [(x["date"], x["service"]) for x in meta["labels"]] == [("2026-02-20", "AWSLambda")]
    _, none = generate_period(date(2026, 1, 1), date(2026, 3, 31), events=())
    assert none["labels"] == []
    assert any(e.kind == "step" for e in DEMO_EVENTS)


def test_multiple_bills_add_up_once_and_planted_anomalies_are_found(client):
    bills = [
        generate_period(date(2026, 1, 1), date(2026, 3, 31)),
        generate_period(date(2026, 3, 1), date(2026, 6, 30), currency="INR", fx=INR),  # overlaps March
        generate_period(date(2026, 7, 1), date(2026, 8, 31)),
    ]
    reports = []
    for i, (df, _) in enumerate(bills):
        r = client.post(
            "/api/v1/ingest/billing",
            params={"format": "csv", "source_name": f"bill{i}.csv"},
            content=_csv(df),
            headers={"Content-Type": "text/csv"},
        )
        assert r.status_code == 201, r.text
        reports.append(r.json())
    assert reports[1]["rows_duplicate"] == 31 * 17  # March, every resource, counted once
    assert all(rep["rows_quarantined"] == 0 for rep in reports)

    # Independent oracle: one line per Id, USD converted at the configured rate.
    frames = []
    for df, _ in bills:
        df = df.copy()
        df["inr"] = df["BilledCost"] * df["BillingCurrency"].map({"USD": INR, "INR": 1.0})
        frames.append(df)
    oracle = pd.concat(frames).drop_duplicates("Id")
    ov = client.get("/api/v1/costs/overview", params={"source": "ingested", "days": 365}).json()
    assert ov["overview"]["total_cost_usd"] == pytest.approx(oracle["inr"].sum(), abs=1.0)
    assert ov["overview"]["period_start"] == "2026-01-01" and ov["overview"]["period_end"] == "2026-08-31"

    labels = {(x["date"], x["provider"], x["service"]) for _, meta in bills for x in meta["labels"]}
    alerts = client.get("/api/v1/anomalies/detect", params={"source": "ingested", "days": 365}).json()["anomalies"]
    found = {(a["date"], a["provider"], a["service"]) for a in alerts}
    assert labels <= found  # every planted spike/step is detected
    assert all(a["currency"] == "INR" for a in alerts)

    workloads = client.get(
        "/api/v1/costs/breakdown", params={"source": "ingested", "days": 365, "dimension": "workload"}
    ).json()["items"]
    assert {"eks-shop/checkout-api", "gke-analytics/recommendation-api"} <= {w["key"] for w in workloads}


def test_restated_line_in_a_later_bill_is_quarantined_not_added(client):
    df, _ = generate_period(date(2026, 4, 1), date(2026, 4, 10), events=())
    client.post(
        "/api/v1/ingest/billing", params={"format": "csv"}, content=_csv(df), headers={"Content-Type": "text/csv"}
    )
    total_before = sum(r.billed_cost for r in ingestion_store.snapshot_records())
    restated = df.iloc[:3].copy()
    restated["BilledCost"] = restated["BilledCost"] * 2  # same Ids, different amounts
    rep = client.post(
        "/api/v1/ingest/billing", params={"format": "csv"}, content=_csv(restated), headers={"Content-Type": "text/csv"}
    ).json()
    assert rep["rows_quarantined"] == 3
    assert rep["quarantine_reasons"] == {"conflicting_duplicate_across_batches": 3}
    assert sum(r.billed_cost for r in ingestion_store.snapshot_records()) == pytest.approx(total_before)
