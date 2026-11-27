"""Idle-resource detection (FR-5).

Rules (thresholds from ``EngineConfig.idle``):

* disks / IP addresses / load balancers: ``attached is False`` -> idle;
  ``attached is None`` -> ``insufficient_data``.
* compute (vm / k8s_workload / database): CPU p95 <= ``cpu_p95_idle_pct`` **and** at
  least one *secondary* activity signal (requests/day or network MB/day) is
  measured and below its limit.  Low CPU without a secondary signal is
  ``requires_review`` (absence of evidence is not evidence of idleness).
* window shorter than ``min_observation_days`` or protected tags -> ``requires_review``.

Safe next action is always reversible first (snapshot + stop), deletion only
after owner confirmation.  Nothing is changed automatically.
"""

from __future__ import annotations

from typing import Any

from finops_engine.engine_config import EngineConfig
from finops_engine.optimization.costs import ResourceCost
from finops_engine.optimization.models import Recommendation, RecommendationStatus, UtilizationSample
from finops_engine.optimization.rightsizing import IDLE_ONLY_TYPES, is_protected

COMPUTE_TYPES = {"vm", "k8s_workload", "database"}


def recommend_idle(
    sample: UtilizationSample,
    cost: ResourceCost | None,
    config: EngineConfig,
    conflicting_resource_ids: set[str] | None = None,
) -> Recommendation | None:
    ic = config.idle
    rtype = sample.resource_type
    tags = {**(cost.tags if cost else {}), **sample.tags}
    rec_id = Recommendation.make_id("idle", sample.resource_id)
    base: dict[str, Any] = {
        "kind": "idle",
        "resource_id": sample.resource_id,
        "provider": sample.provider or (cost.provider if cost else None),
        "service": cost.service if cost else None,
        "config_version": config.version,
        "current_monthly_cost_usd": cost.monthly_cost_usd if cost else None,
    }
    evidence = {
        "resource_type": rtype,
        "attached": sample.attached,
        "cpu_p95_pct": sample.cpu_p95_pct,
        "requests_per_day": sample.requests_per_day,
        "network_mb_per_day": sample.network_mb_per_day,
        "observation_days": sample.observation_days,
        "telemetry_source": sample.source,
        "thresholds": {
            "cpu_p95_idle_pct": ic.cpu_p95_idle_pct,
            "max_requests_per_day": ic.max_requests_per_day,
            "max_network_mb_per_day": ic.max_network_mb_per_day,
        },
    }

    review: list[str] = []
    if rtype in IDLE_ONLY_TYPES:
        if sample.attached is None:
            return Recommendation(
                id=rec_id,
                status=RecommendationStatus.INSUFFICIENT_DATA,
                action="collect_telemetry",
                reason=f"attachment state of {rtype} is unknown",
                assumptions=[],
                estimated_monthly_savings_usd=None,
                confidence="none",
                limitations=["No savings estimated without attachment evidence"],
                safe_next_action="Query the provider inventory for the attachment state, then re-evaluate.",
                evidence=evidence,
                **base,
            )
        if sample.attached:
            return None
        action = "snapshot_then_delete_after_confirmation"
        reason = f"{rtype} is not attached to any running resource"
    elif rtype in COMPUTE_TYPES:
        if sample.cpu_p95_pct is None:
            return None  # right-sizing reports the missing telemetry; avoid duplicate noise
        if sample.cpu_p95_pct > ic.cpu_p95_idle_pct:
            return None
        secondary = []
        if sample.requests_per_day is not None:
            secondary.append(sample.requests_per_day <= ic.max_requests_per_day)
        if sample.network_mb_per_day is not None:
            secondary.append(sample.network_mb_per_day <= ic.max_network_mb_per_day)
        if not secondary:
            review.append("no request/network telemetry to confirm idleness (CPU alone is insufficient)")
        elif not all(secondary):
            return None  # low CPU but real traffic: not idle
        action = "stop_then_decommission_after_confirmation"
        reason = (
            f"CPU p95 {sample.cpu_p95_pct}% <= {ic.cpu_p95_idle_pct}% with "
            f"{sample.requests_per_day} requests/day and {sample.network_mb_per_day} MB/day network"
        )
    else:
        return None

    if sample.observation_days is None or sample.observation_days < ic.min_observation_days:
        review.append(f"observation window {sample.observation_days} days < required {ic.min_observation_days}")
    protected = is_protected(tags, config)
    if protected:
        review.append(f"protected resource ({protected})")
    if conflicting_resource_ids and sample.resource_id in conflicting_resource_ids:
        review.append("conflicting billing attribution for this resource (NT-2)")
    if cost is None or cost.monthly_cost_usd <= 0:
        review.append("no billing cost found for this resource; savings cannot be estimated")

    savings = cost.monthly_cost_usd if cost and cost.monthly_cost_usd > 0 else None
    status = RecommendationStatus.REQUIRES_REVIEW if review else RecommendationStatus.ACTIONABLE
    return Recommendation(
        id=rec_id,
        status=status,
        action=action,
        reason=reason + (f"; review needed: {'; '.join(review)}" if review else ""),
        assumptions=[
            "Savings = full reference monthly cost of the resource once removed",
            "Snapshot/backup storage cost after removal is not included",
        ],
        estimated_monthly_savings_usd=savings,
        confidence="low" if review else "high",
        limitations=["Estimated, not realised, savings", "Scheduled/batch workloads may look idle between runs"],
        safe_next_action=(
            "Notify the owner, take a snapshot/backup, stop (do not delete) for a 14-day grace period, "
            "then delete only after written confirmation."
        ),
        evidence=evidence,
        **base,
    )
