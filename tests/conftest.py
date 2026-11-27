"""Shared test configuration.

Evidence files are redirected to a throw-away directory so test runs never
write into the working tree, and process-wide state (ingestion store, health
registry, circuit breakers, caches) is reset around every test.
"""

import os
import tempfile
from pathlib import Path

# Must happen before finops_engine is imported anywhere.
os.environ.setdefault("FINOP_EVIDENCE_DIR", tempfile.mkdtemp(prefix="finops-test-evidence-"))
# Fixtures and expected values are USD-denominated; the shipped config reports in INR.
os.environ.setdefault(
    "FINOP_ENGINE_CONFIG", str(Path(__file__).resolve().parent.parent / "config" / "finops-engine.usd.json")
)

import pytest  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _reset_global_state():
    from finops_engine.ai.anomaly_detector import clear_detection_cache
    from finops_engine.connectors import fetch_multicloud_cost
    from finops_engine.health import health_registry
    from finops_engine.ingestion import ingestion_store
    from finops_engine.resilience import reset_breakers
    from finops_engine.telemetry import request_stats

    def reset():
        ingestion_store.clear()
        health_registry.reset()
        reset_breakers()
        clear_detection_cache()
        fetch_multicloud_cost.cache_clear()
        request_stats.reset()

    reset()
    yield
    reset()


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


def ingest_file(path: Path, fmt: str = "csv", schema_version: str | None = "1.0"):
    """Helper: ingest a fixture file through the real store; returns the report."""
    from finops_engine.ingestion import ingestion_store, parse_csv_text, parse_json_payload

    raw = path.read_bytes()
    if fmt == "csv":
        rows = parse_csv_text(raw.decode("utf-8"), 1_000_000)
    else:
        import json

        rows, embedded = parse_json_payload(json.loads(raw), 1_000_000)
        schema_version = schema_version or embedded
    return ingestion_store.ingest_rows(
        rows, raw_bytes=raw, source_name=path.name, source_type=f"focus_{fmt}", schema_version=schema_version
    )
