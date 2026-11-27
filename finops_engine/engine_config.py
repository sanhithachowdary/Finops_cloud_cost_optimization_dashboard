"""Versioned analytics / optimisation configuration.

All thresholds that change detection, recommendation or reliability behaviour
live here so that every result can be traced to an explicit, versioned
configuration (FR-3, NFR-5).  Defaults are defined in code; an optional JSON
file (``config/finops-engine.json`` or the path in ``FINOP_ENGINE_CONFIG``)
can override them.  Unknown keys and out-of-range values are rejected at load
time, so a typo can never silently fall back to a default.
"""

from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

logger = logging.getLogger("finops.config")

DEFAULT_CONFIG_PATH = Path("config") / "finops-engine.json"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class IngestionConfig(_Strict):
    supported_schema_versions: tuple[str, ...] = ("1.0", "1.0-preview", "1.1")
    base_currency: str = Field(default="USD", min_length=3, max_length=3)
    # Only currencies listed here are converted to the base currency; anything
    # else is quarantined.  Rates are *operator supplied*; none by default.
    fx_rates_to_base: dict[str, float] = Field(default_factory=dict)
    accepted_usage_units: tuple[str, ...] = (
        "Hours",
        "GB-Hours",
        "GB-Months",
        "GB",
        "Requests",
        "vCPU-Hours",
        "Count",
        "GB-Seconds",
        "Invocations",
    )
    max_abs_cost_per_row: float = Field(default=1_000_000.0, gt=0)
    max_rows_per_batch: int = Field(default=200_000, gt=0)
    max_upload_bytes: int = Field(default=20 * 1024 * 1024, gt=0)
    # Safe mode is entered when more than this share of a batch is quarantined.
    quarantine_ratio_safe_mode: float = Field(default=0.2, ge=0, le=1)
    # Allowed clock skew for timestamps in the future.
    max_future_skew_hours: int = Field(default=48, ge=0)


class AnomalyConfig(_Strict):
    contamination: float = Field(default=0.05, gt=0, le=0.5)
    z_threshold: float = Field(default=3.0, gt=0)
    baseline_window_days: int = Field(default=14, ge=3)
    min_baseline_points: int = Field(default=3, ge=2)
    min_relative_excess: float = Field(default=0.2, ge=0)
    # Absolute materiality floor (USD/day above baseline). Stops cent-level noise on
    # tiny services from becoming alerts (found on the public FOCUS sample).
    min_absolute_excess_usd: float = Field(default=5.0, ge=0)
    severity_high_excess_usd: float = Field(default=200.0, ge=0)
    severity_medium_excess_usd: float = Field(default=50.0, ge=0)


class ForecastConfig(_Strict):
    default_horizon_days: int = Field(default=30, ge=1, le=365)
    min_history_days: int = Field(default=14, ge=3)
    backtest_holdout_days: int = Field(default=14, ge=1)


class RightsizingConfig(_Strict):
    min_observation_days: int = Field(default=14, ge=1)
    cpu_p95_threshold_pct: float = Field(default=40.0, gt=0, le=100)
    mem_p95_threshold_pct: float = Field(default=50.0, gt=0, le=100)
    # Fraction of current cost assumed saved by a one-step downsize within the
    # same family (prices scale ~linearly with vCPU/RAM in most families).
    one_step_savings_fraction: float = Field(default=0.5, gt=0, lt=1)
    max_plausible_savings_fraction: float = Field(default=0.9, gt=0, le=1)
    supported_resource_types: tuple[str, ...] = ("vm", "k8s_workload", "database")


class IdleConfig(_Strict):
    min_observation_days: int = Field(default=7, ge=1)
    cpu_p95_idle_pct: float = Field(default=3.0, ge=0, le=100)
    max_requests_per_day: float = Field(default=5.0, ge=0)
    max_network_mb_per_day: float = Field(default=5.0, ge=0)
    protected_tag_keys: tuple[str, ...] = ("do-not-delete", "finops-exempt")
    protected_tag_values: dict[str, str] = Field(default_factory=lambda: {"env": "prod"})


class CommitmentConfig(_Strict):
    min_history_days: int = Field(default=30, ge=7)
    max_coefficient_of_variation: float = Field(default=0.35, gt=0)
    coverage_target: float = Field(default=0.8, gt=0, le=1)
    eligible_categories: tuple[str, ...] = ("Compute", "Database", "Container")
    # Operator-supplied discount assumptions per provider (fraction off on-demand).
    # These are ASSUMPTIONS for estimation only, never presented as quotes.
    assumed_discount_rate: dict[str, float] = Field(default_factory=lambda: {"AWS": 0.28, "GCP": 0.25, "Azure": 0.30})


class ResilienceConfig(_Strict):
    connector_timeout_seconds: float = Field(default=20.0, gt=0)
    connector_max_retries: int = Field(default=2, ge=0, le=5)
    retry_backoff_seconds: float = Field(default=0.5, ge=0)
    breaker_failure_threshold: int = Field(default=3, ge=1)
    breaker_reset_seconds: float = Field(default=60.0, gt=0)
    telemetry_stale_factor: float = Field(default=3.0, ge=1)


class SLOConfig(_Strict):
    availability_target: float = Field(default=0.99, gt=0, le=1)
    p95_latency_ms_target: float = Field(default=1500.0, gt=0)
    max_error_rate: float = Field(default=0.01, ge=0, le=1)
    max_recovery_seconds: float = Field(default=60.0, gt=0)
    data_integrity_reconciliation_tolerance_usd: float = Field(default=0.01, ge=0)


class EngineConfig(_Strict):
    version: str = "2026.10-v2"
    ingestion: IngestionConfig = IngestionConfig()
    anomaly: AnomalyConfig = AnomalyConfig()
    forecast: ForecastConfig = ForecastConfig()
    rightsizing: RightsizingConfig = RightsizingConfig()
    idle: IdleConfig = IdleConfig()
    commitments: CommitmentConfig = CommitmentConfig()
    resilience: ResilienceConfig = ResilienceConfig()
    slo: SLOConfig = SLOConfig()

    @model_validator(mode="after")
    def _check_cross_field(self) -> EngineConfig:
        if self.anomaly.severity_medium_excess_usd > self.anomaly.severity_high_excess_usd:
            raise ValueError("anomaly.severity_medium_excess_usd must be <= severity_high_excess_usd")
        for provider, rate in self.commitments.assumed_discount_rate.items():
            if not 0 <= rate < 1:
                raise ValueError(f"commitments.assumed_discount_rate[{provider}] must be in [0, 1)")
        for currency, rate in self.ingestion.fx_rates_to_base.items():
            if rate <= 0:
                raise ValueError(f"ingestion.fx_rates_to_base[{currency}] must be > 0")
        return self


def load_engine_config(path: str | os.PathLike[str] | None = None) -> EngineConfig:
    """Load and validate configuration; raises ``ValueError`` on invalid files."""
    explicit = path or os.getenv("FINOP_ENGINE_CONFIG")
    cfg_path = Path(explicit) if explicit else DEFAULT_CONFIG_PATH
    if not cfg_path.exists():
        if explicit:
            raise ValueError(f"FINOP_ENGINE_CONFIG points to a missing file: {cfg_path}")
        logger.info("No engine config file found; using built-in defaults")
        return EngineConfig()
    try:
        raw = json.loads(cfg_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Engine config {cfg_path} is not valid JSON: {exc}") from exc
    config = EngineConfig.model_validate(raw)
    logger.info("Loaded engine config %s (version %s)", cfg_path, config.version)
    return config


@lru_cache(maxsize=1)
def get_engine_config() -> EngineConfig:
    """Process-wide validated configuration (cached)."""
    return load_engine_config()
