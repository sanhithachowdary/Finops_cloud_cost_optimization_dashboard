"""NT-5 Insufficient telemetry / unknown failure (mandatory negative test).

Removes or corrupts expected signals: the metrics refresh fails, telemetry
goes stale, utilisation is missing or corrupt, live mode has no utilisation
source, and SLOs have too few samples.

Expected: the system reports ``failed`` / ``unknown`` / ``insufficient_*``
explicitly and never presents the missing signal as success.
"""

import time

from fastapi.testclient import TestClient

from finops_engine.exporter import metrics_exporter
from finops_engine.health import HealthRegistry, health_registry


def test_metrics_refresh_failure_is_visible(monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("telemetry source corrupted")

    monkeypatch.setattr(metrics_exporter, "fetch_multicloud_cost", broken)
    metrics_exporter.update_finops_metrics()
    assert health_registry.component_status("metrics_refresh") == "failed"
    payload = metrics_exporter.generate_latest().decode()
    assert 'finops_component_status{component="metrics_refresh",status="failed"} 1.0' in payload


def test_stale_telemetry_reported_unknown_not_ok():
    registry = HealthRegistry(stale_factor=1.0)
    registry.register("metrics_refresh", expected_interval_s=0.05)
    registry.record_success("metrics_refresh")
    assert registry.component_status("metrics_refresh") == "ok"
    time.sleep(0.12)
    assert registry.component_status("metrics_refresh") == "unknown"
    snap = registry.snapshot()
    assert snap["overall_status"] != "ok"
    assert "stale" in snap["components"][0]["detail"]


def test_no_signal_is_unknown():
    registry = HealthRegistry()
    assert registry.snapshot()["overall_status"] == "unknown"
    assert registry.component_status("never-reported") == "unknown"


def test_live_mode_without_utilization_says_not_evaluated(monkeypatch):
    from finops_engine.api import data_access
    from finops_engine.api.app import app
    from tests.test_exporter import _focus_record

    monkeypatch.setattr(
        data_access,
        "fetch_multicloud_cost",
        lambda **kw: ([_focus_record("aws-x", billed=5.0, usage=1.0)], "live"),
    )
    with TestClient(app) as client:
        body = client.get("/api/v1/recommendations?source=live&days=30").json()
        assert body["utilization_source"] == "none"
        assert "NOT evaluated" in body["notes"][0]
        assert not [r for r in body["recommendations"] if r["kind"] in ("rightsizing", "idle")]
        health = client.get("/api/v1/ops/health").json()
        util = next(c for c in health["components"] if c["name"] == "utilization_telemetry")
        assert util["status"] == "degraded"


def test_corrupt_utilization_samples_rejected_with_reasons():
    from finops_engine.api.app import app
    from finops_engine.ingestion import ingestion_store

    payload = [
        {"resource_id": "vm-1", "resource_type": "vm", "cpu_p95_pct": 150},
        {"resource_id": "", "cpu_p95_pct": 10},
        {"resource_id": "vm-2", "resource_type": "vm", "cpu_p95_pct": "lots"},
        {"resource_id": "vm-3", "resource_type": "vm", "cpu_p95_pct": 20, "observation_days": 30},
    ]
    import json

    with TestClient(app) as client:
        body = client.post("/api/v1/ingest/utilization", content=json.dumps(payload)).json()
    assert body["accepted"] == 1 and body["rejected"] == 3
    assert all(r["errors"] for r in body["rejections"])
    assert [s.resource_id for s in ingestion_store.snapshot_utilization()] == ["vm-3"]


def test_slo_verdict_requires_enough_evidence():
    from finops_engine.api.app import app

    with TestClient(app) as client:
        body = client.get("/api/v1/ops/slo").json()
    assert body["measurement"]["sample_count"] < 20
    assert body["status"]["p95_latency"] == "insufficient_evidence"
    assert body["status"]["error_rate"] == "insufficient_evidence"


def test_unhandled_error_returns_incident_id_not_stack_trace(monkeypatch):
    from finops_engine.api import routes_extended
    from finops_engine.api.app import app

    def explode():
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(routes_extended.ingestion_store, "quality_summary", explode)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/v1/ops/quality")
    assert response.status_code == 500
    assert "incident_id" in response.json()
    assert "secret internal detail" not in response.text
