"""Integration: ingestion -> analytics -> anomalies/forecast -> recommendations -> savings -> ops (FR-7)."""

import pytest
from fastapi.testclient import TestClient

from finops_engine.api.app import app
from tests.conftest import FIXTURES


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_end_to_end_workflow_on_ingested_benchmark(client):
    raw = (FIXTURES / "billing_benchmark_v1.csv").read_bytes()
    rep = client.post("/api/v1/ingest/billing?format=csv&source_name=benchmark", content=raw)
    assert rep.status_code == 201 and rep.json()["status"] == "accepted"
    util = client.post("/api/v1/ingest/utilization", content=(FIXTURES / "utilization_benchmark_v1.json").read_bytes())
    assert util.json() == {"accepted": 8, "rejected": 0, "rejections": []}

    overview = client.get("/api/v1/costs/overview?source=ingested&days=120").json()
    assert overview["data_source"] == "ingested"
    assert overview["overview"]["days_covered"] == 120

    weekly = client.get("/api/v1/costs/trends?source=ingested&days=120&granularity=weekly").json()
    assert weekly["timezone"] == "UTC" and weekly["series"]
    total = sum(s["cost_usd"] for s in weekly["series"])
    assert total == pytest.approx(overview["overview"]["total_cost_usd"], abs=0.1)

    for dim in ("provider", "account", "service", "region", "resource", "workload"):
        bd = client.get(f"/api/v1/costs/breakdown?source=ingested&days=120&dimension={dim}")
        assert bd.status_code == 200, dim

    anomalies = client.get("/api/v1/anomalies/detect?source=ingested&days=120").json()
    assert anomalies["data_source"] == "ingested" and anomalies["anomalies_detected_count"] > 0
    assert anomalies["anomalies"][0]["alert_id"].startswith("anom-")

    forecast = client.get("/api/v1/forecast/predict?source=ingested&history_days=120&forecast_days=14").json()
    assert forecast["status"] == "ok" and len(forecast["forecast"]) == 14
    backtest = client.get("/api/v1/forecast/backtest?source=ingested&days=120").json()
    assert backtest["status"] == "ok"

    recs = client.get("/api/v1/recommendations?source=ingested&days=120").json()
    kinds = {r["kind"] for r in recs["recommendations"]}
    assert kinds == {"rightsizing", "idle", "commitment"}
    rs = client.get("/api/v1/recommendations/rightsizing?source=ingested&days=120")
    assert rs.status_code == 200  # formerly HTTP 402 teaser
    savings = client.get("/api/v1/savings/summary?source=ingested&days=120").json()
    assert savings["estimated_monthly_savings_usd"] > 0
    assert savings["realized_savings"]["status"] == "not_measured"

    reports = client.get("/api/v1/ingest/reports").json()
    assert reports["quality"]["batches"] == 1
    health = client.get("/api/v1/ops/health").json()
    assert health["mode"] == "normal"


def test_opencost_ingestion_endpoint(client):
    body = (FIXTURES / "opencost_allocation_v1.json").read_bytes()
    rep = client.post("/api/v1/ingest/opencost?provider=AWS", content=body).json()
    assert rep["rows_accepted"] == 3 and rep["utilization_samples_derived"] == 2
    wl = client.get("/api/v1/costs/breakdown?source=ingested&dimension=workload&days=7").json()
    assert {i["key"] for i in wl["items"]} >= {"prod-eks/checkout/deployment/checkout-api", "(unattributed)"}


def test_ingested_source_without_data_is_404(client):
    assert client.get("/api/v1/costs/overview?source=ingested").status_code == 404


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/costs/breakdown?dimension=colour",
        "/api/v1/costs/trends?granularity=hourly",
        "/api/v1/costs/overview?days=0",
        "/api/v1/costs/overview?source=s3",
        "/api/v1/recommendations?kind=magic",
        "/api/v1/forecast/backtest?holdout_days=0",
    ],
)
def test_query_validation(client, path):
    assert client.get(path).status_code == 422


def test_auth_boundary_on_new_endpoints(client, monkeypatch):
    from finops_engine.config import settings

    monkeypatch.setattr(settings, "api_key", "k-test")
    for path in ("/api/v1/costs/overview?source=mock", "/api/v1/recommendations?source=mock", "/api/v1/ops/health"):
        assert client.get(path).status_code == 401
        assert client.get(path, headers={"X-API-Key": "k-test"}).status_code == 200
    assert client.post("/api/v1/ingest/billing", content=b"a,b").status_code == 401
    # The dashboard shell and liveness probe carry no data and stay public.
    assert client.get("/dashboard").status_code == 200
    assert client.get("/healthz").status_code == 200


def test_dashboard_is_self_contained_with_security_headers(client):
    r = client.get("/dashboard")
    assert "text/html" in r.headers["content-type"]
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    for view in ("overview", "spend", "anomalies", "forecast", "optimization", "savings", "operations"):
        assert f'data-view="{view}"' in r.text
    assert "https://" not in r.text.split("<script>")[1]  # no external script/CDN calls


def test_latency_metrics_and_slo_after_traffic(client):
    for _ in range(25):
        client.get("/api/v1/costs/overview?source=mock&days=7")
    slo = client.get("/api/v1/ops/slo").json()
    assert slo["measurement"]["sample_count"] >= 25
    assert slo["status"]["p95_latency"] in ("met", "breached")
    metrics = client.get("/metrics").text
    assert 'finops_api_request_duration_seconds_count{method="GET",route="/api/v1/costs/overview"}' in metrics
