"""Per-resource reference cost used by every recommendation (one formula, one place).

Reference monthly cost = mean daily billed cost of the resource over the
trailing ``window_days`` (default 30) days that have data × 30.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from finops_engine.schema.focus_spec import ChargeCategory, FocusRecord

DAYS_PER_MONTH = 30


@dataclass(frozen=True)
class ResourceCost:
    resource_id: str
    provider: str
    service: str
    service_category: str
    account: str
    monthly_cost_usd: float
    days_observed: int
    tags: dict[str, str]


def resource_costs(records: list[FocusRecord], window_days: int = 30) -> dict[str, ResourceCost]:
    usage = [r for r in records if r.resource_id and r.charge_category == ChargeCategory.USAGE]
    if not usage:
        return {}
    latest = max(r.billing_period_start for r in usage)
    cutoff = latest - timedelta(days=window_days - 1)
    per: dict[str, dict] = {}
    for r in usage:
        if r.billing_period_start < cutoff:
            continue
        rid = str(r.resource_id)
        entry = per.setdefault(
            rid,
            {"cost": 0.0, "days": set(), "rec": r, "tags": {}},
        )
        entry["cost"] += r.billed_cost
        entry["days"].add(r.billing_period_start.date())
        entry["tags"].update(r.tags)
    out: dict[str, ResourceCost] = {}
    for rid, e in per.items():
        days = len(e["days"])
        rec: FocusRecord = e["rec"]
        out[rid] = ResourceCost(
            resource_id=rid,
            provider=rec.provider_name.value,
            service=rec.service_name,
            service_category=rec.service_category,
            account=rec.sub_account_id,
            monthly_cost_usd=round(e["cost"] / days * DAYS_PER_MONTH, 2) if days else 0.0,
            days_observed=days,
            tags=dict(e["tags"]),
        )
    return out


def reference_monthly_cost(records: list[FocusRecord], window_days: int = 30) -> dict[str, object]:
    """Trailing-window spend normalised to 30 days (the savings reference)."""
    if not records:
        return {"monthly_cost_usd": 0.0, "window_days": 0, "window_start": None, "window_end": None}
    latest = max(r.billing_period_start for r in records)
    cutoff = latest - timedelta(days=window_days - 1)
    window = [r for r in records if r.billing_period_start >= cutoff]
    days = {r.billing_period_start.date() for r in window}
    total = sum(r.billed_cost for r in window)
    return {
        "monthly_cost_usd": round(total / len(days) * DAYS_PER_MONTH, 2) if days else 0.0,
        "window_days": len(days),
        "window_start": min(days).isoformat(),
        "window_end": max(days).isoformat(),
        "formula": "sum(billed_cost in trailing window) / days_with_data * 30",
    }
