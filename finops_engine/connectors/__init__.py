"""
Connector gateway for multi-cloud cost telemetry (Community Edition).

Provides a single, fail-closed entry point that aggregates AWS, GCP, and Azure
cost data, plus the mock connector used for demos and tests.
"""

import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Protocol

from cachetools import TTLCache, cached

from finops_engine.config import settings
from finops_engine.currency import to_base_currency
from finops_engine.engine_config import get_engine_config
from finops_engine.errors import DataFetchError
from finops_engine.health import health_registry
from finops_engine.resilience import call_with_resilience, get_breaker
from finops_engine.schema.focus_spec import FocusRecord

from .aws_connector import AWSConnector
from .azure_connector import AzureConnector
from .gcp_connector import GCPConnector
from .mock_connector import MockTelemetryConnector

__all__ = [
    "AWSConnector",
    "GCPConnector",
    "AzureConnector",
    "MockTelemetryConnector",
    "DataFetchError",
    "fetch_multicloud_cost",
]


logger = logging.getLogger("finops.connectors")


def _base(records: list[FocusRecord]) -> list[FocusRecord]:
    """Connectors bill in USD; convert to the configured reporting currency (no-op for USD)."""
    return to_base_currency(records, get_engine_config().ingestion)


class CostConnector(Protocol):
    """Structural type shared by every live cloud connector."""

    def fetch_cost_data(self, start_date: str | None = None, end_date: str | None = None) -> list[FocusRecord]: ...


# condition= gives single-flight semantics: concurrent identical requests wait
# for one in-flight fetch instead of duplicating cloud API calls (NFR-4).
@cached(cache=TTLCache(maxsize=32, ttl=3600), condition=threading.Condition())
def fetch_multicloud_cost(
    use_mock: bool = True,
    days: int = 30,
    allow_fallback: bool = False,
) -> tuple[list[FocusRecord], str]:
    """
    Fetch cost telemetry across all configured cloud providers.

    Returns a ``(records, source)`` tuple where ``source`` is one of
    ``"mock"``, ``"live"``, or ``"mock-fallback"``.

    Fails closed: when live fetching returns no records and ``allow_fallback``
    is False, a ``DataFetchError`` is raised instead of silently reporting
    synthetic data as real.
    """
    if use_mock:
        return _base(MockTelemetryConnector(days=days).fetch_cost_data()), "mock"

    end_dt = datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(days=days)
    start_date = start_dt.strftime("%Y-%m-%d")
    end_date = end_dt.strftime("%Y-%m-%d")

    connectors: list[CostConnector] = []
    if settings.aws_account_id:
        connectors.append(AWSConnector(region_name=settings.aws_region, account_id=settings.aws_account_id))
    if settings.gcp_project_id and settings.gcp_billing_table:
        connectors.append(GCPConnector(project_id=settings.gcp_project_id, billing_table=settings.gcp_billing_table))
    if settings.azure_subscription_id:
        connectors.append(AzureConnector(subscription_id=settings.azure_subscription_id))

    if not connectors:
        if allow_fallback:
            return _base(MockTelemetryConnector(days=days).fetch_cost_data()), "mock-fallback"
        raise DataFetchError("No live cloud providers are configured")
    records: list[FocusRecord] = []
    errors: list[str] = []

    resilience = get_engine_config().resilience
    for connector in connectors:
        provider_name = connector.__class__.__name__
        component = f"connector:{provider_name}"
        try:
            fetched, _log = call_with_resilience(
                component,
                lambda c=connector: c.fetch_cost_data(start_date=start_date, end_date=end_date),  # type: ignore[misc]
                timeout_s=resilience.connector_timeout_seconds,
                retries=resilience.connector_max_retries,
                backoff_s=resilience.retry_backoff_seconds,
                breaker=get_breaker(component, resilience.breaker_failure_threshold, resilience.breaker_reset_seconds),
            )
            if not fetched:
                health_registry.record_failure(component, "connector returned no records", degraded_only=True)
            records.extend(fetched)
        except Exception as exc:
            logger.error("Connector %s failed: %s", provider_name, exc)
            errors.append(provider_name)

    if not records:
        if allow_fallback:
            return _base(MockTelemetryConnector(days=days).fetch_cost_data()), "mock-fallback"
        raise DataFetchError(f"No cost data available from any configured cloud provider. Failures: {errors}")

    source = "live"
    if errors:
        source = f"live (partial failure: {', '.join(errors)})"

    return _base(records), source
