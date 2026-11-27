"""Spend analytics, ingestion, optimisation, savings and operations endpoints.

Mounted under ``/api/v1`` by :mod:`finops_engine.api.app`, so every route here
inherits the API-key dependency.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.concurrency import run_in_threadpool
from pydantic import ValidationError

from finops_engine import __version__
from finops_engine.analytics import period_comparison, spend_breakdown, spend_overview, spend_trend
from finops_engine.api.data_access import load_dataset
from finops_engine.engine_config import get_engine_config
from finops_engine.evaluation import backtest_forecast
from finops_engine.exporter import (
    FINOPS_ESTIMATED_SAVINGS,
    FINOPS_INGESTION_ROWS,
    FINOPS_RECOMMENDATIONS,
    FINOPS_REQUEST_COUNTER,
    publish_health_metrics,
)
from finops_engine.health import health_registry
from finops_engine.ingestion import (
    BatchRejectedError,
    ingestion_store,
    opencost_to_rows,
    parse_csv_text,
    parse_json_payload,
)
from finops_engine.optimization.engine import generate_recommendations
from finops_engine.optimization.models import UtilizationSample
from finops_engine.resilience import breaker_states
from finops_engine.telemetry import request_stats

logger = logging.getLogger("finops.api.extended")

router = APIRouter()

SourceParam = Query(default="auto", pattern="^(auto|mock|live|ingested)$")


def _count(endpoint: str) -> None:
    FINOPS_REQUEST_COUNTER.labels(endpoint=endpoint).inc()


# --------------------------------------------------------------------- spend
@router.get("/costs/overview")
def costs_overview(
    days: int = Query(default=30, ge=1, le=730),
    source: str = SourceParam,
    use_mock: bool | None = None,
    metric: Literal["billed_cost", "effective_cost"] = "billed_cost",
):
    """Headline spend, period-over-period change and attribution quality."""
    _count("/api/v1/costs/overview")
    ds = load_dataset(source, use_mock, days)
    half = max(1, days // 2)
    return {
        "data_source": ds.source,
        "period_days": days,
        "overview": spend_overview(ds.records, metric),
        "comparison": period_comparison(ds.records, half, metric),
        "by_provider": spend_breakdown(ds.records, "provider", metric),
        "attribution": {
            "unattributed_share": spend_breakdown(ds.records, "resource", metric)["unattributed_share"],
            "conflicting_resource_ids": sorted(ds.conflicting_resource_ids),
        },
    }


@router.get("/costs/trends")
def costs_trends(
    granularity: Literal["daily", "weekly", "monthly"] = "daily",
    days: int = Query(default=90, ge=1, le=730),
    metric: Literal["billed_cost", "effective_cost"] = "billed_cost",
    split_by_provider: bool = False,
    source: str = SourceParam,
    use_mock: bool | None = None,
):
    """Spend per UTC day / ISO week / calendar month."""
    _count("/api/v1/costs/trends")
    ds = load_dataset(source, use_mock, days)
    return {
        "data_source": ds.source,
        "granularity": granularity,
        "metric": metric,
        "timezone": "UTC",
        "series": spend_trend(ds.records, granularity, metric, split_by_provider),
    }


@router.get("/costs/breakdown")
def costs_breakdown(
    dimension: Literal[
        "provider", "account", "service", "category", "region", "resource", "workload", "charge_category"
    ] = "service",
    days: int = Query(default=30, ge=1, le=730),
    metric: Literal["billed_cost", "effective_cost"] = "billed_cost",
    top_n: int | None = Query(default=None, ge=1, le=500),
    source: str = SourceParam,
    use_mock: bool | None = None,
):
    """Spend by provider, account, service, region, resource or Kubernetes workload."""
    _count("/api/v1/costs/breakdown")
    ds = load_dataset(source, use_mock, days)
    result = spend_breakdown(ds.records, dimension, metric, top_n, ds.conflicting_resource_ids)
    result["data_source"] = ds.source
    return result


# ----------------------------------------------------------------- ingestion
async def _read_body_limited(request: Request, limit: int) -> bytes:
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > limit:
                raise HTTPException(status_code=413, detail=f"payload exceeds {limit} bytes")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid Content-Length") from exc
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise HTTPException(status_code=413, detail=f"payload exceeds {limit} bytes")
        chunks.append(chunk)
    body = b"".join(chunks)
    if not body:
        raise HTTPException(status_code=422, detail="empty request body")
    return body


def _record_ingestion_metrics(report: dict[str, Any]) -> None:
    if report.get("idempotent_replay"):
        FINOPS_INGESTION_ROWS.labels(outcome="replayed_batch").inc(report.get("rows_received", 0))
        return
    FINOPS_INGESTION_ROWS.labels(outcome="accepted").inc(report.get("rows_accepted", 0))
    FINOPS_INGESTION_ROWS.labels(outcome="quarantined").inc(report.get("rows_quarantined", 0))
    FINOPS_INGESTION_ROWS.labels(outcome="duplicate").inc(report.get("rows_duplicate", 0))
    publish_health_metrics()


@router.post("/ingest/billing", status_code=status.HTTP_201_CREATED)
async def ingest_billing(
    request: Request,
    format: Literal["csv", "json"] = "csv",
    source_name: str = Query(default="upload", max_length=200),
    schema_version: str | None = Query(default=None, max_length=20),
):
    """Ingest a FOCUS-style billing export (raw CSV or JSON body). Idempotent per file content."""
    _count("/api/v1/ingest/billing")
    cfg = get_engine_config()
    body = await _read_body_limited(request, cfg.ingestion.max_upload_bytes)

    def _work() -> dict[str, Any]:
        try:
            text = body.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise HTTPException(status_code=422, detail="body must be UTF-8 text") from exc
        try:
            if format == "csv":
                rows, version = parse_csv_text(text, cfg.ingestion.max_rows_per_batch), schema_version
            else:
                rows, embedded = parse_json_payload(json.loads(text), cfg.ingestion.max_rows_per_batch)
                version = schema_version or embedded
        except (ValueError, json.JSONDecodeError) as exc:
            health_registry.record_failure("ingestion", f"unparseable {format} batch", degraded_only=True)
            raise HTTPException(status_code=422, detail=f"could not parse {format} payload: {exc}") from exc
        try:
            report = ingestion_store.ingest_rows(
                rows,
                raw_bytes=body,
                source_name=source_name,
                source_type=f"focus_{format}",
                schema_version=version,
                config=cfg,
            )
        except BatchRejectedError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        _record_ingestion_metrics(report)
        return report

    return await run_in_threadpool(_work)


@router.post("/ingest/opencost", status_code=status.HTTP_201_CREATED)
async def ingest_opencost(
    request: Request,
    provider: Literal["AWS", "GCP", "Azure"] | None = None,
    currency: str = Query(default="USD", min_length=3, max_length=3),
    account_id: str | None = Query(default=None, max_length=200),
    source_name: str = Query(default="opencost", max_length=200),
):
    """Ingest an exported OpenCost /allocation response (no live OpenCost call is made)."""
    _count("/api/v1/ingest/opencost")
    cfg = get_engine_config()
    body = await _read_body_limited(request, cfg.ingestion.max_upload_bytes)

    def _work() -> dict[str, Any]:
        try:
            payload = json.loads(body.decode("utf-8-sig"))
            rows, samples = opencost_to_rows(
                payload, default_provider=provider, currency=currency.upper(), account_id=account_id
            )
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            health_registry.record_failure("ingestion", "unparseable OpenCost payload", degraded_only=True)
            raise HTTPException(status_code=422, detail=f"invalid OpenCost payload: {exc}") from exc
        report = ingestion_store.ingest_rows(
            rows,
            raw_bytes=body,
            source_name=source_name,
            source_type="opencost_allocation",
            schema_version="1.0",
            config=cfg,
            utilization=samples,
        )
        report["utilization_samples_derived"] = len(samples)
        _record_ingestion_metrics(report)
        return report

    return await run_in_threadpool(_work)


@router.post("/ingest/utilization", status_code=status.HTTP_201_CREATED)
async def ingest_utilization(request: Request):
    """Ingest utilisation samples (JSON list or {"samples": [...]}) for right-sizing/idle analysis."""
    _count("/api/v1/ingest/utilization")
    cfg = get_engine_config()
    body = await _read_body_limited(request, cfg.ingestion.max_upload_bytes)
    try:
        payload = json.loads(body.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail="body must be UTF-8 JSON") from exc
    items = payload.get("samples") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        raise HTTPException(status_code=422, detail="expected a list of samples")
    accepted: list[UtilizationSample] = []
    rejected: list[dict[str, Any]] = []
    for idx, item in enumerate(items):
        if isinstance(item, dict):
            item = {k: v for k, v in item.items() if k != "expected"}
        try:
            accepted.append(UtilizationSample.model_validate(item))
        except ValidationError as exc:
            rejected.append(
                {"index": idx, "errors": [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()]}
            )
    ingestion_store.add_utilization(accepted)
    if accepted:
        health_registry.record_success("utilization_telemetry", f"{len(accepted)} samples ingested")
    return {"accepted": len(accepted), "rejected": len(rejected), "rejections": rejected[:100]}


@router.get("/ingest/reports")
async def ingestion_reports(limit: int = Query(default=20, ge=1, le=200)):
    """Latest ingestion batch reports (reconciliation, quarantine reasons, attribution quality)."""
    _count("/api/v1/ingest/reports")
    return {"quality": ingestion_store.quality_summary(), "batches": ingestion_store.reports(limit)}


# --------------------------------------------------------------- optimisation
def _recommendations(source: str, use_mock: bool | None, days: int) -> dict[str, Any]:
    ds = load_dataset(source, use_mock, days)
    cfg = get_engine_config()
    snapshot = health_registry.snapshot()
    result = generate_recommendations(
        ds.records,
        ds.utilization,
        cfg,
        conflicting_resource_ids=ds.conflicting_resource_ids,
        safe_mode=snapshot["mode"] == "safe",
        safe_mode_reason="; ".join(snapshot["safe_mode_reasons"].values()) or None,
    )
    result["data_source"] = ds.source
    result["utilization_source"] = ds.utilization_source
    if not ds.utilization:
        result["notes"] = [
            "No utilisation telemetry available for this source: right-sizing and idle detection were "
            "NOT evaluated (unknown, not 'no savings'). Ingest samples via /api/v1/ingest/utilization."
        ]
    FINOPS_RECOMMENDATIONS.clear()
    for kind, counts in result["summary"]["counts"].items():
        for st, n in counts.items():
            FINOPS_RECOMMENDATIONS.labels(kind=kind, status=st).set(n)
    FINOPS_ESTIMATED_SAVINGS.clear()
    for kind, values in result["summary"]["by_kind"].items():
        FINOPS_ESTIMATED_SAVINGS.labels(kind=kind).set(values["actionable"])
    return result


@router.get("/recommendations")
def list_recommendations(
    kind: Literal["rightsizing", "idle", "commitment"] | None = None,
    status_filter: str | None = Query(default=None, alias="status", max_length=32),
    days: int = Query(default=60, ge=7, le=730),
    source: str = SourceParam,
    use_mock: bool | None = None,
):
    """All optimisation recommendations (advisory only; nothing is applied automatically)."""
    _count("/api/v1/recommendations")
    result = _recommendations(source, use_mock, days)
    recs = result["recommendations"]
    if kind:
        recs = [r for r in recs if r["kind"] == kind]
    if status_filter:
        recs = [r for r in recs if r["status"] == status_filter]
    result["recommendations"] = recs
    return result


@router.get("/recommendations/rightsizing")
def rightsizing_recommendations(
    days: int = Query(default=60, ge=7, le=730), source: str = SourceParam, use_mock: bool | None = None
):
    """Right-sizing recommendations (replaces the former HTTP 402 Enterprise teaser)."""
    _count("/api/v1/recommendations/rightsizing")
    result = _recommendations(source, use_mock, days)
    result["recommendations"] = [r for r in result["recommendations"] if r["kind"] == "rightsizing"]
    return result


@router.get("/savings/summary")
def savings_summary_endpoint(
    days: int = Query(default=60, ge=7, le=730), source: str = SourceParam, use_mock: bool | None = None
):
    """Estimated vs realised savings with reference cost, formula and overlap controls."""
    _count("/api/v1/savings/summary")
    result = _recommendations(source, use_mock, days)
    return {"data_source": result["data_source"], "config_version": result["config_version"], **result["summary"]}


@router.get("/forecast/backtest")
def forecast_backtest(
    holdout_days: int = Query(default=14, ge=1, le=60),
    days: int = Query(default=120, ge=30, le=730),
    source: str = SourceParam,
    use_mock: bool | None = None,
):
    """Forecast accuracy on held-out days (MAE/RMSE/MAPE) vs naive baselines."""
    _count("/api/v1/forecast/backtest")
    ds = load_dataset(source, use_mock, days)
    result = backtest_forecast(ds.records, holdout_days, get_engine_config().forecast.min_history_days)
    result["data_source"] = ds.source
    return result


# ----------------------------------------------------------------- operations
@router.get("/ops/health")
async def ops_health():
    """Component health, degraded/safe mode, breaker states and recent recovery events."""
    _count("/api/v1/ops/health")
    snapshot = health_registry.snapshot()
    snapshot["circuit_breakers"] = breaker_states()
    snapshot["config_version"] = get_engine_config().version
    snapshot["version"] = __version__
    snapshot["operator_guidance"] = _operator_guidance(snapshot)
    return snapshot


@router.get("/ops/slo")
async def ops_slo(window_seconds: int | None = Query(default=None, ge=10, le=86400)):
    """Measured request latency/error rate vs configured SLO targets (in-process sample)."""
    _count("/api/v1/ops/slo")
    cfg = get_engine_config().slo
    stats = request_stats.summary(window_seconds, exclude_routes=("/metrics",))
    snapshot = health_registry.snapshot()

    def verdict(measured: float | None, target: float, lower_is_better: bool = True) -> str:
        if measured is None or stats["sample_count"] < 20:
            return "insufficient_evidence"
        return "met" if (measured <= target if lower_is_better else measured >= target) else "breached"

    return {
        "measurement": stats,
        "targets": cfg.model_dump(),
        "status": {
            "p95_latency": verdict(stats["p95_ms"], cfg.p95_latency_ms_target),
            "error_rate": verdict(stats["server_error_rate"], cfg.max_error_rate),
            "data_integrity": "breached" if snapshot["mode"] == "safe" else "met",
            "availability": "not_measured_in_process (use external probes / Prometheus up metric)",
        },
        "note": "In-process ring buffer of recent requests; at least 20 samples are required for a verdict.",
    }


@router.get("/ops/quality")
async def ops_quality():
    """Ingestion data-quality summary."""
    _count("/api/v1/ops/quality")
    return ingestion_store.quality_summary()


def _operator_guidance(snapshot: dict[str, Any]) -> list[str]:
    tips: list[str] = []
    if snapshot["mode"] == "safe":
        tips.append(
            "SAFE MODE: recommendations are suppressed because ingested data failed integrity checks. "
            "Inspect /api/v1/ingest/reports and the quarantine evidence files, fix the export, re-ingest."
        )
    for comp in snapshot["components"]:
        if comp["status"] == "failed" and comp["name"].startswith("connector:"):
            tips.append(f"{comp['name']} failing: check credentials/network; breaker fails fast until it resets.")
        if comp["status"] == "unknown":
            tips.append(f"{comp['name']}: no recent signal; treat its outputs as unverified.")
        if comp["name"] == "utilization_telemetry" and comp["status"] != "ok":
            tips.append("Utilisation telemetry missing: right-sizing/idle results are incomplete.")
    return tips or ["All monitored components report healthy."]
