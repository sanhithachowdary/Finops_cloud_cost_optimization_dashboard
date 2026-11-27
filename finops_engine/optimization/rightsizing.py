"""Right-sizing recommendations (FR-4).

Contract
--------
Input : :class:`UtilizationSample` per resource + billing records (for cost).
Output: zero or one :class:`Recommendation` per sample.

Decision rules (thresholds from ``EngineConfig.rightsizing``):

1. resource type not supported for right-sizing  -> ``rejected``
2. no CPU percentile telemetry                     -> ``insufficient_data`` (no savings claimed)
3. CPU p95 >= threshold or memory p95 >= threshold -> no recommendation (healthy)
4. no billing cost for the resource                -> ``requires_review`` (savings unknown)
5. observation window shorter than the minimum, memory not measured,
   protected tag, or conflicting attribution      -> ``requires_review``
6. estimated savings implausible (<= 0 or above the plausibility cap)  -> ``rejected``
7. otherwise                                       -> ``actionable``

Estimated savings = reference monthly cost × ``one_step_savings_fraction``
(one size step down in the same family).  Nothing is ever applied
automatically.
"""

from __future__ import annotations

from typing import Any

from finops_engine.currency import format_money
from finops_engine.engine_config import EngineConfig
from finops_engine.optimization.costs import ResourceCost
from finops_engine.optimization.models import Recommendation, RecommendationStatus, UtilizationSample

IDLE_ONLY_TYPES = {"disk", "ip_address", "load_balancer"}


def is_protected(tags: dict[str, str], config: EngineConfig) -> str | None:
    for key in config.idle.protected_tag_keys:
        if key in tags:
            return f"tag '{key}' present"
    for key, value in config.idle.protected_tag_values.items():
        if tags.get(key, "").lower() == value.lower():
            return f"tag {key}={value}"
    return None


def recommend_rightsizing(
    sample: UtilizationSample,
    cost: ResourceCost | None,
    config: EngineConfig,
    conflicting_resource_ids: set[str] | None = None,
) -> Recommendation | None:
    rs = config.rightsizing
    cur = config.ingestion.base_currency
    rtype = sample.resource_type
    if rtype in IDLE_ONLY_TYPES:
        return None  # handled by the idle engine
    tags = {**(cost.tags if cost else {}), **sample.tags}
    base: dict[str, Any] = {
        "kind": "rightsizing",
        "resource_id": sample.resource_id,
        "provider": sample.provider or (cost.provider if cost else None),
        "service": cost.service if cost else None,
        "config_version": config.version,
        "current_monthly_cost_usd": cost.monthly_cost_usd if cost else None,
    }
    evidence = {
        "resource_type": rtype,
        "current_size": sample.size,
        "cpu_p95_pct": sample.cpu_p95_pct,
        "cpu_avg_pct": sample.cpu_avg_pct,
        "mem_p95_pct": sample.mem_p95_pct,
        "observation_days": sample.observation_days,
        "telemetry_source": sample.source,
        "percentiles_are_proxies": sample.percentiles_are_proxies,
        "thresholds": {"cpu_p95_pct": rs.cpu_p95_threshold_pct, "mem_p95_pct": rs.mem_p95_threshold_pct},
        "billing_days_observed": cost.days_observed if cost else 0,
    }
    rec_id = Recommendation.make_id("rightsizing", sample.resource_id)

    if rtype not in rs.supported_resource_types:
        return Recommendation(
            id=rec_id,
            status=RecommendationStatus.REJECTED,
            action="none",
            reason=f"resource type '{rtype}' is not supported for right-sizing",
            assumptions=[],
            estimated_monthly_savings_usd=None,
            confidence="none",
            limitations=[f"supported types: {list(rs.supported_resource_types)}"],
            safe_next_action="No action. Review manually if this resource type should be sized.",
            evidence=evidence,
            **base,
        )

    if sample.cpu_p95_pct is None:
        return Recommendation(
            id=rec_id,
            status=RecommendationStatus.INSUFFICIENT_DATA,
            action="collect_telemetry",
            reason="CPU utilisation percentile telemetry is missing; utilisation is UNKNOWN, not low",
            assumptions=[],
            estimated_monthly_savings_usd=None,
            confidence="none",
            limitations=["No savings estimated without utilisation evidence"],
            safe_next_action="Enable CPU/memory metrics collection (e.g. CloudWatch agent, Azure Monitor, "
            "GCP Ops Agent or OpenCost) and re-evaluate after the minimum observation window.",
            evidence=evidence,
            **base,
        )

    cpu_low = sample.cpu_p95_pct < rs.cpu_p95_threshold_pct
    mem_low = sample.mem_p95_pct is None or sample.mem_p95_pct < rs.mem_p95_threshold_pct
    if not (cpu_low and mem_low):
        return None  # healthy utilisation: no recommendation

    review: list[str] = []
    limitations: list[str] = []
    assumptions = [
        f"One size step down within the same family saves ~{int(rs.one_step_savings_fraction * 100)}% "
        "(linear price scaling assumption; verify with the provider price list)",
        "Reference cost = trailing 30-day billed cost of this resource normalised to 30 days",
        "Peak (p95) utilisation after downsizing is expected to roughly double and must stay below 80%",
    ]
    if cost is None or cost.monthly_cost_usd <= 0:
        review.append("no billing cost found for this resource; savings cannot be estimated")
    if sample.observation_days is None or sample.observation_days < rs.min_observation_days:
        review.append(f"observation window {sample.observation_days} days < required {rs.min_observation_days} days")
    if sample.mem_p95_pct is None:
        review.append("memory utilisation not measured; downsizing could cause memory pressure")
    protected = is_protected(tags, config)
    if protected:
        review.append(f"protected resource ({protected}); owner approval required")
    if conflicting_resource_ids and sample.resource_id in conflicting_resource_ids:
        review.append("conflicting billing attribution for this resource (NT-2)")
    if sample.percentiles_are_proxies:
        limitations.append("p95 values are averages used as proxies (OpenCost); peaks may be higher")

    savings = round(cost.monthly_cost_usd * rs.one_step_savings_fraction, 2) if cost else None
    if savings is not None and (savings <= 0 or savings > cost.monthly_cost_usd * rs.max_plausible_savings_fraction):  # type: ignore[union-attr]
        return Recommendation(
            id=rec_id,
            status=RecommendationStatus.REJECTED,
            action="none",
            reason=f"implausible savings estimate {format_money(savings, cur)} for monthly cost "
            f"{format_money(cost.monthly_cost_usd, cur)}",  # type: ignore[union-attr]
            assumptions=assumptions,
            estimated_monthly_savings_usd=None,
            confidence="none",
            limitations=["Estimate failed plausibility check; no savings claimed"],
            safe_next_action="Investigate the billing and utilisation inputs before re-evaluating.",
            evidence=evidence,
            **base,
        )

    strong = (
        sample.cpu_p95_pct < 0.75 * rs.cpu_p95_threshold_pct
        and sample.observation_days is not None
        and sample.observation_days >= 2 * rs.min_observation_days
        and not sample.percentiles_are_proxies
        and sample.mem_p95_pct is not None
    )
    status = RecommendationStatus.REQUIRES_REVIEW if review else RecommendationStatus.ACTIONABLE
    confidence = "low" if review else ("high" if strong else "medium")
    return Recommendation(
        id=rec_id,
        status=status,
        action="downsize_one_step",
        reason=(
            f"CPU p95 {sample.cpu_p95_pct}% < {rs.cpu_p95_threshold_pct}% and memory p95 "
            f"{sample.mem_p95_pct if sample.mem_p95_pct is not None else 'unknown'}% "
            f"< {rs.mem_p95_threshold_pct}% over {sample.observation_days} days"
            + (f"; review needed: {'; '.join(review)}" if review else "")
        ),
        assumptions=assumptions,
        estimated_monthly_savings_usd=savings,
        confidence=confidence,
        limitations=limitations
        + ["Estimated, not realised, savings", "Does not account for licensing or reserved capacity already held"],
        safe_next_action=(
            "Create a change request: resize one step in a staging/canary instance first, monitor p95 "
            "CPU/memory and error rates for 7 days, keep a rollback to the original size."
        ),
        evidence=evidence,
        **base,
    )
