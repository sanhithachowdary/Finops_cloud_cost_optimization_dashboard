"""NT-4 Latency / resource overload (mandatory negative test).

Simulates, entirely locally: a slow dependency (sleeps longer than its
timeout), an erroring dependency, worker-pool saturation and recovery.

Expected: each call is bounded in time and attempts; the circuit breaker
opens and then fails fast; saturation is rejected immediately instead of
queueing; the API degrades to a clear 503 / partial result rather than
hanging; once the dependency is healthy again the breaker closes and a
recovery event with its duration is recorded.
"""

import threading
import time

import pytest

from finops_engine.config import settings
from finops_engine.connectors import AWSConnector, GCPConnector, fetch_multicloud_cost
from finops_engine.engine_config import EngineConfig
from finops_engine.errors import DataFetchError
from finops_engine.health import health_registry
from finops_engine.resilience import (
    MAX_WORKERS,
    CircuitBreaker,
    CircuitOpenError,
    DependencyTimeoutError,
    SaturatedError,
    call_with_resilience,
    in_flight,
)

FAST = EngineConfig.model_validate(
    {
        "resilience": {
            "connector_timeout_seconds": 0.2,
            "connector_max_retries": 1,
            "retry_backoff_seconds": 0.0,
            "breaker_failure_threshold": 2,
            "breaker_reset_seconds": 0.3,
        }
    }
)


@pytest.fixture
def fast_config(monkeypatch):
    import finops_engine.connectors as connectors

    monkeypatch.setattr(connectors, "get_engine_config", lambda: FAST)
    monkeypatch.setattr(settings, "aws_account_id", "123456789012")
    monkeypatch.setattr(settings, "gcp_project_id", "")
    monkeypatch.setattr(settings, "gcp_billing_table", "")
    monkeypatch.setattr(settings, "azure_subscription_id", "")
    return FAST


def _slow(seconds):
    def fetch(self, *args, **kwargs):
        time.sleep(seconds)
        return []

    return fetch


def test_slow_dependency_is_bounded_in_time_and_attempts(fast_config, monkeypatch):
    monkeypatch.setattr(AWSConnector, "fetch_cost_data", _slow(1.5))
    started = time.monotonic()
    with pytest.raises(DataFetchError):
        fetch_multicloud_cost(use_mock=False, days=7)
    elapsed = time.monotonic() - started
    # 2 attempts x 0.2 s timeout (+ scheduling slack) - far below the 3 s an unbounded call would take
    assert elapsed < 1.0
    comp = next(c for c in health_registry.snapshot()["components"] if c["name"] == "connector:AWSConnector")
    assert comp["status"] == "failed"
    assert "2 attempt" in comp["detail"]


def test_breaker_opens_fails_fast_then_recovers(fast_config, monkeypatch):
    monkeypatch.setattr(AWSConnector, "fetch_cost_data", _slow(1.0))
    for _ in range(2):  # threshold = 2 failures
        fetch_multicloud_cost.cache_clear()
        with pytest.raises(DataFetchError):
            fetch_multicloud_cost(use_mock=False, days=7)

    fetch_multicloud_cost.cache_clear()
    started = time.monotonic()
    with pytest.raises(DataFetchError):
        fetch_multicloud_cost(use_mock=False, days=7)
    assert time.monotonic() - started < 0.1  # open breaker: no dependency call at all

    # Dependency heals; after the reset window the half-open probe succeeds.
    from tests.test_exporter import _focus_record

    monkeypatch.setattr(
        AWSConnector, "fetch_cost_data", lambda self, *a, **k: [_focus_record("aws-x", billed=5.0, usage=1.0)]
    )
    time.sleep(0.35)
    fetch_multicloud_cost.cache_clear()
    records, source = fetch_multicloud_cost(use_mock=False, days=7)
    assert source == "live" and len(records) == 1
    snapshot = health_registry.snapshot()
    recovered = [e for e in snapshot["recent_events"] if e["event"] == "recovered"]
    assert recovered and recovered[-1]["component"] == "connector:AWSConnector"
    assert recovered[-1]["recovery_seconds"] > 0
    assert snapshot["recoveries_total"] >= 1


def test_partial_failure_is_labelled_not_hidden(fast_config, monkeypatch):
    from tests.test_exporter import _focus_record

    monkeypatch.setattr(settings, "gcp_project_id", "proj")
    monkeypatch.setattr(settings, "gcp_billing_table", "proj.billing.export")
    monkeypatch.setattr(
        AWSConnector, "fetch_cost_data", lambda self, *a, **k: [_focus_record("aws-x", billed=5.0, usage=1.0)]
    )

    def boom(self, *a, **k):
        raise RuntimeError("simulated outage")

    monkeypatch.setattr(GCPConnector, "fetch_cost_data", boom)
    records, source = fetch_multicloud_cost(use_mock=False, days=7)
    assert len(records) == 1
    assert source == "live (partial failure: GCPConnector)"


def test_saturation_is_rejected_immediately():
    # Timed-out calls from earlier tests keep their worker until they finish
    # (documented behaviour); wait for the pool to drain first.
    deadline = time.monotonic() + 5
    while in_flight() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert in_flight() == 0

    release = threading.Event()
    breaker = CircuitBreaker(failure_threshold=100)
    threads = [
        threading.Thread(
            target=call_with_resilience,
            args=("dep:block", lambda: release.wait(5)),
            kwargs={"timeout_s": 5, "retries": 0, "breaker": breaker},
        )
        for _ in range(MAX_WORKERS)
    ]
    try:
        for t in threads:
            t.start()
        deadline = time.monotonic() + 2
        while in_flight() < MAX_WORKERS and time.monotonic() < deadline:
            time.sleep(0.01)
        assert in_flight() == MAX_WORKERS
        started = time.monotonic()
        with pytest.raises(SaturatedError):
            call_with_resilience("dep:extra", lambda: 1, timeout_s=1, retries=0)
        assert time.monotonic() - started < 0.05
    finally:
        release.set()
        for t in threads:
            t.join()
    # capacity is restored after the pressure is gone
    result, log = call_with_resilience("dep:extra", lambda: 42, timeout_s=1, retries=0)
    assert result == 42


def test_retry_count_is_bounded():
    calls = []

    def flaky():
        calls.append(1)
        raise RuntimeError("boom")

    with pytest.raises(Exception, match="failed after 3 attempt"):
        call_with_resilience("dep:flaky", flaky, timeout_s=1, retries=2, breaker=CircuitBreaker(100))
    assert len(calls) == 3


def test_open_breaker_raises_without_calling():
    breaker = CircuitBreaker(failure_threshold=1, reset_s=60)
    breaker.on_failure()
    called = []
    with pytest.raises(CircuitOpenError):
        call_with_resilience("dep:open", lambda: called.append(1), timeout_s=1, retries=0, breaker=breaker)
    assert called == []


def test_timeout_error_type():
    with pytest.raises(DependencyTimeoutError):
        call_with_resilience("dep:slow", lambda: time.sleep(0.5), timeout_s=0.05, retries=0, breaker=CircuitBreaker(9))


def test_api_returns_503_quickly_when_live_dependency_hangs(fast_config, monkeypatch):
    from fastapi.testclient import TestClient

    from finops_engine.api.app import app

    monkeypatch.setattr(AWSConnector, "fetch_cost_data", _slow(2.0))
    with TestClient(app) as client:
        started = time.monotonic()
        response = client.get("/api/v1/costs/overview?source=live&days=7")
        assert response.status_code == 503
        assert time.monotonic() - started < 1.5
        health = client.get("/api/v1/ops/health").json()
        assert health["mode"] in ("degraded", "safe")
        assert any("connector:AWSConnector" in tip for tip in health["operator_guidance"])
