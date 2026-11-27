"""Data contracts for utilisation inputs and optimisation recommendations."""

from __future__ import annotations

import hashlib
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ResourceType(str, Enum):
    VM = "vm"
    K8S_WORKLOAD = "k8s_workload"
    DATABASE = "database"
    DISK = "disk"
    IP_ADDRESS = "ip_address"
    LOAD_BALANCER = "load_balancer"
    OTHER = "other"


class UtilizationSample(BaseModel):
    """Utilisation telemetry for one resource over an observation window.

    Percentages are 0–100.  ``None`` means *not measured*, which is
    deliberately different from ``0`` (measured as idle).
    """

    model_config = ConfigDict(extra="forbid")

    resource_id: str = Field(..., min_length=1, max_length=512)
    provider: str | None = None
    resource_type: str = Field(default=ResourceType.VM.value)
    size: str | None = Field(default=None, max_length=128)
    cpu_avg_pct: float | None = Field(default=None, ge=0, le=100)
    cpu_p95_pct: float | None = Field(default=None, ge=0, le=100)
    mem_avg_pct: float | None = Field(default=None, ge=0, le=100)
    mem_p95_pct: float | None = Field(default=None, ge=0, le=100)
    network_mb_per_day: float | None = Field(default=None, ge=0)
    requests_per_day: float | None = Field(default=None, ge=0)
    observation_days: float | None = Field(default=None, ge=0)
    attached: bool | None = Field(default=None, description="For disks/IPs: attached to a running resource?")
    percentiles_are_proxies: bool = False
    tags: dict[str, str] = Field(default_factory=dict)
    source: str = "fixture"

    @field_validator("resource_type")
    @classmethod
    def _lower(cls, v: str) -> str:
        return v.strip().lower()


class RecommendationStatus(str, Enum):
    ACTIONABLE = "actionable"
    REQUIRES_REVIEW = "requires_review"
    INSUFFICIENT_DATA = "insufficient_data"
    REJECTED = "rejected"
    SUPPRESSED = "suppressed"  # e.g. safe mode / overlapping recommendation


class Recommendation(BaseModel):
    """A single, never-auto-applied optimisation recommendation."""

    id: str
    kind: str  # rightsizing | idle | commitment
    status: RecommendationStatus
    resource_id: str | None
    provider: str | None
    service: str | None
    action: str
    reason: str
    assumptions: list[str]
    current_monthly_cost_usd: float | None
    estimated_monthly_savings_usd: float | None
    confidence: str  # high | medium | low | none
    limitations: list[str]
    safe_next_action: str
    evidence: dict[str, Any]
    config_version: str
    auto_apply: bool = False  # recommendations never change resources

    @staticmethod
    def make_id(kind: str, key: str) -> str:
        return f"{kind}-{hashlib.sha256(key.encode()).hexdigest()[:12]}"
