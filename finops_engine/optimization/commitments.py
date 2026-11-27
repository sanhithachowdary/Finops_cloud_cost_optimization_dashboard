"""Commitment / discount opportunity recommendations (FR-6).

Only produced when the data supports them:

* service category must be eligible (Compute / Database / Container by default)
* at least ``min_history_days`` of daily usage history
* a configured (assumed) discount rate for the provider — otherwise no savings are invented
* coefficient of variation above the limit, or evidence of existing discounts
  (effective cost >10% below billed) -> ``requires_review``

Commitment size = P10 of daily on-demand cost (a conservative, spike-robust
baseline) × ``coverage_target``, *after* subtracting savings already claimed by
right-sizing / idle recommendations for the same provider+service
(double-counting control).
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import date
from typing import Any

from finops_engine.currency import format_money
from finops_engine.engine_config import EngineConfig
from finops_engine.optimization.costs import DAYS_PER_MONTH
from finops_engine.optimization.models import Recommendation, RecommendationStatus
from finops_engine.schema.focus_spec import ChargeCategory, FocusRecord

CANDIDATE_TYPES = {
    ("AWS", "Compute"): "Compute Savings Plan (1-year, no upfront)",
    ("AWS", "Container"): "Compute Savings Plan (1-year, no upfront)",
    ("AWS", "Database"): "Reserved DB instances (1-year)",
    ("GCP", "Compute"): "Committed use discount (1-year)",
    ("GCP", "Container"): "Committed use discount (1-year)",
    ("GCP", "Database"): "Committed use discount (1-year)",
    ("Azure", "Compute"): "Azure savings plan for compute (1-year)",
    ("Azure", "Container"): "Azure savings plan for compute (1-year)",
    ("Azure", "Database"): "Reserved capacity (1-year)",
}


def _p10(values: list[float]) -> float:
    ordered = sorted(values)
    idx = max(0, int(0.1 * (len(ordered) - 1)))
    return ordered[idx]


def recommend_commitments(
    records: list[FocusRecord],
    config: EngineConfig,
    claimed_monthly_savings: dict[tuple[str, str], float] | None = None,
) -> list[Recommendation]:
    cc = config.commitments
    cur = config.ingestion.base_currency
    claimed = claimed_monthly_savings or {}
    daily: dict[tuple[str, str, str], dict[date, list[float]]] = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0]))
    for r in records:
        if r.charge_category != ChargeCategory.USAGE or r.service_category not in cc.eligible_categories:
            continue
        key = (r.provider_name.value, r.service_name, r.service_category)
        cell = daily[key][r.billing_period_start.date()]
        cell[0] += r.billed_cost
        cell[1] += r.effective_cost

    out: list[Recommendation] = []
    for (provider, service, category), by_day in sorted(daily.items()):
        days = sorted(by_day)[-cc.min_history_days * 3 :]
        billed = [by_day[d][0] for d in days]
        effective = [by_day[d][1] for d in days]
        rec_id = Recommendation.make_id("commitment", f"{provider}|{service}")
        base: dict[str, Any] = {
            "kind": "commitment",
            "resource_id": None,
            "provider": provider,
            "service": service,
            "config_version": config.version,
        }
        evidence: dict[str, object] = {"history_days": len(days), "service_category": category}
        if len(days) < cc.min_history_days:
            out.append(
                Recommendation(
                    id=rec_id,
                    status=RecommendationStatus.INSUFFICIENT_DATA,
                    action="collect_history",
                    reason=f"{len(days)} days of usage history; {cc.min_history_days} required",
                    assumptions=[],
                    current_monthly_cost_usd=None,
                    estimated_monthly_savings_usd=None,
                    confidence="none",
                    limitations=["No commitment sized without sufficient history"],
                    safe_next_action="Re-evaluate once enough history is available.",
                    evidence=evidence,
                    **base,
                )
            )
            continue

        mean = statistics.fmean(billed)
        cv = statistics.pstdev(billed) / mean if mean > 0 else float("inf")
        p10 = _p10(billed)
        existing_discount = 1 - (sum(effective) / sum(billed)) if sum(billed) > 0 else 0.0
        claimed_daily = claimed.get((provider, service), 0.0) / DAYS_PER_MONTH
        baseline_daily = max(0.0, p10 - claimed_daily)
        commit_daily = baseline_daily * cc.coverage_target
        rate = cc.assumed_discount_rate.get(provider)
        evidence.update(
            {
                "mean_daily_cost_usd": round(mean, 2),
                "p10_daily_cost_usd": round(p10, 2),
                "coefficient_of_variation": round(cv, 4),
                "existing_discount_share": round(existing_discount, 4),
                "claimed_by_other_recommendations_daily_usd": round(claimed_daily, 2),
                "commitment_daily_usd": round(commit_daily, 2),
                "commitment_hourly_usd": round(commit_daily / 24, 4),
                "assumed_discount_rate": rate,
            }
        )
        if rate is None:
            out.append(
                Recommendation(
                    id=rec_id,
                    status=RecommendationStatus.INSUFFICIENT_DATA,
                    action="configure_discount_assumption",
                    reason=f"no discount rate configured for {provider}; savings would be invented",
                    assumptions=[],
                    current_monthly_cost_usd=round(mean * DAYS_PER_MONTH, 2),
                    estimated_monthly_savings_usd=None,
                    confidence="none",
                    limitations=["Configure commitments.assumed_discount_rate from your negotiated price list"],
                    safe_next_action="Add the provider's discount rate to the engine config.",
                    evidence=evidence,
                    **base,
                )
            )
            continue
        if commit_daily <= 0:
            continue
        savings = round(commit_daily * DAYS_PER_MONTH * rate, 2)
        review = []
        if cv > cc.max_coefficient_of_variation:
            review.append(f"usage is volatile (CV {cv:.2f} > {cc.max_coefficient_of_variation})")
        if existing_discount > 0.10:
            review.append(
                f"effective cost is {existing_discount:.0%} below billed: existing commitments/discounts "
                "likely; savings may overlap"
            )
        status = RecommendationStatus.REQUIRES_REVIEW if review else RecommendationStatus.ACTIONABLE
        candidate = CANDIDATE_TYPES.get((provider, category), "1-year commitment")
        out.append(
            Recommendation(
                id=rec_id,
                status=status,
                action="evaluate_commitment_purchase",
                reason=(
                    f"Stable baseline usage: P10 {format_money(p10, cur)}/day over {len(days)} days (CV {cv:.2f}); "
                    f"candidate: {candidate} covering {format_money(commit_daily, cur)}/day"
                    + (f"; review needed: {'; '.join(review)}" if review else "")
                ),
                assumptions=[
                    f"ASSUMED discount {rate:.0%} vs on-demand for {provider} (configurable; not a price quote)",
                    f"Coverage target {cc.coverage_target:.0%} of the P10 daily baseline",
                    "Baseline reduced by savings already claimed by right-sizing/idle recommendations",
                    "Billed cost treated as on-demand cost",
                ],
                current_monthly_cost_usd=round(mean * DAYS_PER_MONTH, 2),
                estimated_monthly_savings_usd=savings,
                confidence="low" if review else "medium",
                limitations=[
                    "Commitments are financial obligations for the full term; verify with procurement",
                    "Estimated, not realised, savings",
                ],
                safe_next_action=(
                    "Validate the baseline in the provider's own recommendation tool, confirm the price with "
                    "procurement, then purchase a smaller commitment first and ladder up."
                ),
                evidence=evidence,
                **base,
            )
        )
    return out
