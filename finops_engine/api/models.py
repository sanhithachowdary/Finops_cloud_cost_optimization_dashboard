"""Pydantic response models for the Community FinOps API endpoints."""

from typing import Any

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """System health check and OpenAPI service metadata."""

    status: str
    service: str
    version: str
    focus_compliance: str
    documentation: str
    prometheus_metrics: str


class CostSummaryResponse(BaseModel):
    """Aggregated spend summary across multi-cloud providers, services, and regions."""

    period_days: int
    total_billed_cost_usd: float
    spend_by_provider: dict[str, float]
    spend_by_service: dict[str, float]
    spend_by_region: dict[str, float]
    total_records_processed: int
    data_source: str


class AnomalyItem(BaseModel):
    """Individual spend anomaly detection event with root-cause insights."""

    date: str
    provider: str
    service: str
    actual_cost_usd: float
    expected_baseline_usd: float
    anomaly_excess_usd: float
    z_score: float
    severity: str
    root_cause: str
    # Extended alert contract (FR-3); optional for backward compatibility.
    alert_id: str | None = None
    status: str | None = None
    affected_entity: dict[str, Any] | None = None
    window_start: str | None = None
    window_end: str | None = None
    observed_cost_usd: float | None = None
    expected_cost_usd: float | None = None
    score: float | None = None
    reason: str | None = None
    detection_method: str | None = None
    evidence: dict[str, Any] | None = None
    config_version: str | None = None
    currency: str | None = None  # reporting currency of every amount in this alert


class AnomalyDetectionResponse(BaseModel):
    """Detection results across the requested historical analysis window."""

    analyzed_days: int
    anomalies_detected_count: int
    data_source: str
    anomalies: list[AnomalyItem]


class ForecastItem(BaseModel):
    """Projected daily cost data point with 95% confidence bounds."""

    date: str
    predicted_cost_usd: float
    confidence_upper_usd: float
    confidence_lower_usd: float


class ForecastResponse(BaseModel):
    """Forward-looking spend forecast with summary metrics."""

    forecast_days: int
    total_projected_spend_usd: float
    average_daily_projected_usd: float
    confidence_level: str
    data_source: str
    forecast: list[ForecastItem]
    status: str | None = None
    status_reason: str | None = None
    method: str | None = None
    history_days: int | None = None
    trend_usd_per_day: float | None = None
