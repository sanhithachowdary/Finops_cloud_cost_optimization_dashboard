"""Orchestrates all optimisation engines and the savings ledger.

Double-counting controls
------------------------
1. One resource -> at most one *counted* resource-level recommendation.  If an
   idle recommendation is actionable, the right-sizing recommendation for the
   same resource is ``suppressed`` (you cannot downsize something you remove).
2. Commitment baselines are reduced by savings already claimed by actionable
   resource-level recommendations for the same provider+service.
3. Only ``actionable`` recommendations count towards estimated savings;
   ``requires_review`` estimates are reported separately as *potential*.
4. Safe mode (data-integrity doubt) suppresses every recommendation.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from finops_engine.engine_config import EngineConfig
from finops_engine.optimization.commitments import recommend_commitments
from finops_engine.optimization.costs import reference_monthly_cost, resource_costs
from finops_engine.optimization.idle import recommend_idle
from finops_engine.optimization.models import Recommendation, RecommendationStatus, UtilizationSample
from finops_engine.optimization.rightsizing import recommend_rightsizing
from finops_engine.schema.focus_spec import FocusRecord


def _suppress(rec: Recommendation, reason: str) -> Recommendation:
    return rec.model_copy(
        update={
            "status": RecommendationStatus.SUPPRESSED,
            "reason": f"{reason} (original: {rec.reason})",
            "confidence": "none",
        }
    )


def generate_recommendations(
    records: list[FocusRecord],
    utilization: list[UtilizationSample],
    config: EngineConfig,
    *,
    conflicting_resource_ids: set[str] | None = None,
    safe_mode: bool = False,
    safe_mode_reason: str | None = None,
) -> dict[str, Any]:
    costs = resource_costs(records)
    conflicts = conflicting_resource_ids or set()

    idle: list[Recommendation] = []
    rightsizing: list[Recommendation] = []
    evaluated = 0
    for sample in utilization:
        evaluated += 1
        cost = costs.get(sample.resource_id)
        idle_rec = recommend_idle(sample, cost, config, conflicts)
        if idle_rec:
            idle.append(idle_rec)
        rs_rec = recommend_rightsizing(sample, cost, config, conflicts)
        if rs_rec:
            if idle_rec and idle_rec.status in (RecommendationStatus.ACTIONABLE, RecommendationStatus.REQUIRES_REVIEW):
                # Removing the resource supersedes resizing it; keeping both would double-count the
                # same resource in estimated or "potential" savings.
                rs_rec = _suppress(
                    rs_rec, f"overlaps an {idle_rec.status.value} idle recommendation for the same resource"
                )
            rightsizing.append(rs_rec)

    claimed: dict[tuple[str, str], float] = defaultdict(float)
    for rec in idle + rightsizing:
        if rec.status == RecommendationStatus.ACTIONABLE and rec.estimated_monthly_savings_usd and rec.resource_id:
            cost = costs.get(rec.resource_id)
            if cost:
                claimed[(cost.provider, cost.service)] += rec.estimated_monthly_savings_usd
    commitments = recommend_commitments(records, config, claimed)

    all_recs = rightsizing + idle + commitments
    if safe_mode:
        reason = f"safe mode active: {safe_mode_reason or 'data integrity in doubt'}"
        all_recs = [_suppress(r, reason) for r in all_recs]

    summary = savings_summary(records, all_recs)
    summary["resources_evaluated"] = evaluated
    summary["safe_mode"] = safe_mode
    return {
        "config_version": config.version,
        "recommendations": [r.model_dump(mode="json") for r in all_recs],
        "summary": summary,
    }


def savings_summary(records: list[FocusRecord], recs: list[Recommendation]) -> dict[str, Any]:
    reference = reference_monthly_cost(records)
    by_kind: dict[str, dict[str, float]] = defaultdict(lambda: {"actionable": 0.0, "potential_requires_review": 0.0})
    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for rec in recs:
        counts[rec.kind][rec.status.value] += 1
        if rec.estimated_monthly_savings_usd is None:
            continue
        if rec.status == RecommendationStatus.ACTIONABLE:
            by_kind[rec.kind]["actionable"] += rec.estimated_monthly_savings_usd
        elif rec.status == RecommendationStatus.REQUIRES_REVIEW:
            by_kind[rec.kind]["potential_requires_review"] += rec.estimated_monthly_savings_usd
    actionable = sum(v["actionable"] for v in by_kind.values())
    potential = sum(v["potential_requires_review"] for v in by_kind.values())
    ref = float(reference["monthly_cost_usd"])  # type: ignore[arg-type]
    return {
        "reference": reference,
        "estimated_monthly_savings_usd": round(actionable, 2),
        "potential_monthly_savings_requires_review_usd": round(potential, 2),
        "estimated_relative_savings_pct": round(100 * actionable / ref, 2) if ref > 0 else None,
        "by_kind": {k: {kk: round(vv, 2) for kk, vv in v.items()} for k, v in by_kind.items()},
        "counts": {k: dict(v) for k, v in counts.items()},
        "realized_savings": {
            "status": "not_measured",
            "monthly_usd": None,
            "reason": "No recommendation has been implemented and no post-change billing data is available. "
            "Use realized_savings() with before/after billing to measure.",
        },
        "formula": "estimated_relative_savings_pct = sum(actionable estimated monthly savings) / "
        "reference monthly cost × 100",
        "double_counting_controls": [
            "idle supersedes right-sizing for the same resource",
            "commitment baseline reduced by claimed resource-level savings",
            "requires_review estimates excluded from the estimated total",
        ],
    }


def realized_savings(
    records: list[FocusRecord], resource_ids: set[str], change_date: str, window_days: int = 14
) -> dict[str, Any]:
    """Measured (not estimated) savings: mean daily cost of the given resources in
    the ``window_days`` before vs after ``change_date`` (YYYY-MM-DD, UTC)."""
    from datetime import date, timedelta

    pivot = date.fromisoformat(change_date)
    before: dict[date, float] = defaultdict(float)
    after: dict[date, float] = defaultdict(float)
    for r in records:
        if r.resource_id not in resource_ids:
            continue
        d = r.billing_period_start.date()
        if pivot - timedelta(days=window_days) <= d < pivot:
            before[d] += r.billed_cost
        elif pivot <= d < pivot + timedelta(days=window_days):
            after[d] += r.billed_cost
    if not before or len(after) < window_days:
        return {
            "status": "not_measured",
            "reason": f"need {window_days} days of billing before and after {change_date}; "
            f"have {len(before)} before / {len(after)} after",
            "monthly_usd": None,
        }
    mean_before = sum(before.values()) / len(before)
    mean_after = sum(after.values()) / len(after)
    return {
        "status": "measured",
        "window_days": window_days,
        "mean_daily_before_usd": round(mean_before, 2),
        "mean_daily_after_usd": round(mean_after, 2),
        "monthly_usd": round((mean_before - mean_after) * 30, 2),
        "caveat": "Attributes the whole before/after difference to the change; confounders are not removed.",
    }
