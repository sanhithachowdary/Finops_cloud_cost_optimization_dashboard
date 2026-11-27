"""Resolve which dataset an analytics request runs on (mock / live / ingested)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta

from fastapi import HTTPException, status

from finops_engine.config import settings
from finops_engine.connectors import fetch_multicloud_cost
from finops_engine.connectors.mock_connector import mock_utilization
from finops_engine.errors import ConnectorError
from finops_engine.health import health_registry
from finops_engine.ingestion import ingestion_store
from finops_engine.optimization.models import UtilizationSample
from finops_engine.schema.focus_spec import FocusRecord

logger = logging.getLogger("finops.api.data")

SOURCES = ("auto", "mock", "live", "ingested")


@dataclass
class Dataset:
    records: list[FocusRecord]
    source: str
    utilization: list[UtilizationSample] = field(default_factory=list)
    utilization_source: str = "none"
    conflicting_resource_ids: set[str] = field(default_factory=set)


def load_dataset(source: str = "auto", use_mock: bool | None = None, days: int = 30) -> Dataset:
    if source not in SOURCES:
        raise HTTPException(status_code=422, detail=f"source must be one of {list(SOURCES)}")
    if source == "ingested":
        records = ingestion_store.snapshot_records()
        if not records:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No ingested billing data. POST a billing export to /api/v1/ingest/billing first.",
            )
        latest = max(r.billing_period_start for r in records)
        cutoff = latest - timedelta(days=days - 1)
        window = [r for r in records if r.billing_period_start >= cutoff.replace(hour=0, minute=0, second=0)]
        utilization = ingestion_store.snapshot_utilization()
        if utilization:
            health_registry.record_success("utilization_telemetry", f"{len(utilization)} ingested samples")
        else:
            health_registry.record_failure(
                "utilization_telemetry", "no utilisation samples ingested", degraded_only=True
            )
        return Dataset(
            records=window,
            source="ingested",
            utilization=utilization,
            utilization_source="ingested" if utilization else "none",
            conflicting_resource_ids=set(ingestion_store.conflicting_resource_ids),
        )

    mock_mode = settings.mock_mode if use_mock is None else use_mock
    if source == "mock":
        mock_mode = True
    elif source == "live":
        mock_mode = False
    try:
        records, label = fetch_multicloud_cost(
            use_mock=mock_mode, days=days, allow_fallback=settings.allow_mock_fallback
        )
    except ConnectorError as exc:
        logger.warning("No cost data available: %s", exc)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    if label in ("mock", "mock-fallback"):
        health_registry.record_success("utilization_telemetry", "SYNTHETIC mock utilisation samples")
        return Dataset(
            records=records, source=label, utilization=mock_utilization(), utilization_source="mock-synthetic"
        )
    # Live billing connectors do not collect utilisation metrics: say so explicitly.
    health_registry.record_failure(
        "utilization_telemetry",
        "live mode has no utilisation telemetry source; right-sizing/idle cannot be evaluated",
        degraded_only=True,
    )
    return Dataset(records=records, source=label)
