"""Prometheus metrics for the FinOps engine (Community Edition).

Metrics are recomputed on a background loop so /metrics stays fast and never
blocks on model fitting, cached between refreshes, and label sets are cleared
each cycle to avoid stale series.
"""

import asyncio
import logging
import time
from typing import TypedDict

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

from finops_engine.ai.anomaly_detector import AnomalyDetector, detect_anomalies_cached
from finops_engine.config import settings
from finops_engine.connectors import fetch_multicloud_cost
from finops_engine.engine_config import get_engine_config
from finops_engine.health import Status, health_registry

logger = logging.getLogger("finops.exporter")

__all__ = [
    "CONTENT_TYPE_LATEST",
    "FINOPS_REQUEST_COUNTER",
    "FINOPS_REQUEST_LATENCY",
    "FINOPS_RESPONSES",
    "FINOPS_RECOMMENDATIONS",
    "FINOPS_ESTIMATED_SAVINGS",
    "FINOPS_INGESTION_ROWS",
    "get_prometheus_metrics_bytes",
    "publish_health_metrics",
    "refresh_finops_metrics_loop",
    "update_finops_metrics",
]

FINOPS_COST_GAUGE = Gauge(
    "finops_cloud_cost_usd",
    "Current accumulated cloud cost in USD",
    ["provider", "service"],
)

FINOPS_ANOMALIES_GAUGE = Gauge(
    "finops_anomalies_active_count",
    "Active detected cloud cost anomalies count",
    ["severity"],
)

FINOPS_REQUEST_COUNTER = Counter(
    "finops_api_requests_total",
    "Total API requests served by FinOps Engine",
    ["endpoint"],
)


FINOPS_REQUEST_LATENCY = Histogram(
    "finops_api_request_duration_seconds",
    "API request latency in seconds",
    ["route", "method"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

FINOPS_RESPONSES = Counter(
    "finops_api_responses_total",
    "API responses by route and status class",
    ["route", "status_class"],
)

FINOPS_COMPONENT_STATUS = Gauge(
    "finops_component_status",
    "One-hot component health status (1 for the current status)",
    ["component", "status"],
)

FINOPS_SAFE_MODE = Gauge("finops_safe_mode", "1 when the engine is in safe mode (recommendations suppressed)")

FINOPS_TELEMETRY_LAST_SUCCESS = Gauge(
    "finops_metrics_refresh_last_success_timestamp_seconds",
    "Unix time of the last successful metrics refresh (0 = never)",
)

FINOPS_RECOMMENDATIONS = Gauge(
    "finops_recommendations",
    "Recommendations by kind and status from the latest evaluation",
    ["kind", "status"],
)

FINOPS_ESTIMATED_SAVINGS = Gauge(
    "finops_estimated_monthly_savings_usd",
    "Estimated (not realised) monthly savings of actionable recommendations",
    ["kind"],
)

FINOPS_INGESTION_ROWS = Counter(
    "finops_ingestion_rows_total",
    "Billing rows processed by ingestion outcome",
    ["outcome"],
)


class MetricsCache(TypedDict):
    """In-memory cache for Prometheus serialized scrape payload."""

    content: bytes | None
    last_refresh: float


_metrics_cache: MetricsCache = {"content": None, "last_refresh": 0.0}


def update_finops_metrics() -> None:
    """Fetches latest telemetry, runs AI anomaly detector, and updates Prometheus metrics."""
    try:
        records, _ = fetch_multicloud_cost(use_mock=settings.mock_mode, days=30, allow_fallback=True)

        FINOPS_COST_GAUGE.clear()
        FINOPS_ANOMALIES_GAUGE.clear()

        cost_map: dict[tuple[str, str], float] = {}
        for record in records:
            key = (record.provider_name.value, record.service_name)
            cost_map[key] = cost_map.get(key, 0.0) + record.billed_cost

        for (provider, service), cost in cost_map.items():
            FINOPS_COST_GAUGE.labels(provider=provider, service=service).set(round(cost, 2))

        cfg = get_engine_config()
        detector = AnomalyDetector.from_config(cfg.anomaly, cfg.version)
        anomalies = detect_anomalies_cached(detector, records, "metrics")
        severity_counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
        for anomaly in anomalies:
            sev = anomaly.get("severity", "LOW")
            severity_counts[sev] = severity_counts.get(sev, 0) + 1

        for sev, count in severity_counts.items():
            FINOPS_ANOMALIES_GAUGE.labels(severity=sev).set(count)

        FINOPS_TELEMETRY_LAST_SUCCESS.set(time.time())
        health_registry.record_success("metrics_refresh", f"{len(records)} records, {len(anomalies)} anomalies")
    except Exception as exc:
        # Previously swallowed silently; now visible as a failed component so
        # stale metrics are never presented as healthy (NT-5).
        logger.exception("Failed to update Prometheus metrics")
        health_registry.record_failure("metrics_refresh", f"refresh failed: {type(exc).__name__}")
    finally:
        publish_health_metrics()


def publish_health_metrics() -> None:
    """Mirror the health registry into Prometheus gauges."""
    snapshot = health_registry.snapshot()
    FINOPS_COMPONENT_STATUS.clear()
    for component in snapshot["components"]:
        for status in Status:
            FINOPS_COMPONENT_STATUS.labels(component=component["name"], status=status.value).set(
                1 if component["status"] == status.value else 0
            )
    FINOPS_SAFE_MODE.set(1 if snapshot["mode"] == "safe" else 0)


def _refresh_cached_metrics() -> None:
    """Updates the internal Prometheus registry and stores the serialized scrape payload."""
    update_finops_metrics()
    _metrics_cache["content"] = generate_latest()
    _metrics_cache["last_refresh"] = time.monotonic()


def get_prometheus_metrics_bytes() -> bytes:
    """Returns the latest cached Prometheus metrics payload for HTTP handlers."""
    if _metrics_cache["content"] is None:
        _refresh_cached_metrics()
    content = _metrics_cache["content"]
    if content is None:
        raise RuntimeError("Metrics cache was not populated")
    return content


async def refresh_finops_metrics_loop(interval_seconds: int | None = None) -> None:
    """Background task that periodically refreshes the Prometheus metrics cache."""
    interval = max(5, interval_seconds if interval_seconds is not None else settings.metrics_refresh_seconds)
    health_registry.register("metrics_refresh", expected_interval_s=interval)
    health_registry.stale_factor = get_engine_config().resilience.telemetry_stale_factor
    while True:
        try:
            await asyncio.to_thread(_refresh_cached_metrics)
        except Exception:
            logger.exception("Background Prometheus metrics refresh failed")
        await asyncio.sleep(interval)
