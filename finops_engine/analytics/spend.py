"""Spend analytics: trends and breakdowns over FOCUS records (FR-2).

All periods are computed in **UTC** from ``billing_period_start``:
* daily   – calendar day
* weekly  – ISO week starting Monday 00:00 UTC
* monthly – calendar month

Records that cannot be attributed to an account/resource are reported in an
explicit ``(unattributed)`` bucket instead of being dropped or spread, and
resources with conflicting attribution are flagged (NT-2).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import pandas as pd

from finops_engine.ingestion.validator import PLACEHOLDER_ACCOUNTS
from finops_engine.schema.focus_spec import FocusRecord, normalize_to_focus_dataframe

Granularity = Literal["daily", "weekly", "monthly"]
Metric = Literal["billed_cost", "effective_cost"]
DIMENSIONS = ("provider", "account", "service", "category", "region", "resource", "workload", "charge_category")
UNATTRIBUTED = "(unattributed)"
NOT_K8S = "(not a Kubernetes workload)"


def _frame(records: list[FocusRecord]) -> pd.DataFrame:
    df = normalize_to_focus_dataframe(records)
    if df.empty:
        return df
    df["start_utc"] = pd.to_datetime(df["billing_period_start"], utc=True)
    return df


def _period_start(ts: pd.Timestamp, granularity: Granularity) -> datetime:
    day = datetime(ts.year, ts.month, ts.day, tzinfo=timezone.utc)
    if granularity == "daily":
        return day
    if granularity == "weekly":
        return day - timedelta(days=day.weekday())
    return datetime(ts.year, ts.month, 1, tzinfo=timezone.utc)


def _label(start: datetime, granularity: Granularity) -> str:
    if granularity == "daily":
        return start.strftime("%Y-%m-%d")
    if granularity == "weekly":
        iso = start.isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"
    return start.strftime("%Y-%m")


def spend_trend(
    records: list[FocusRecord],
    granularity: Granularity = "daily",
    metric: Metric = "billed_cost",
    split_by_provider: bool = False,
) -> list[dict[str, Any]]:
    """Cost per period (optionally per provider), oldest first."""
    if granularity not in ("daily", "weekly", "monthly"):
        raise ValueError(f"unsupported granularity {granularity!r}")
    if metric not in ("billed_cost", "effective_cost"):
        raise ValueError(f"unsupported metric {metric!r}")
    df = _frame(records)
    if df.empty:
        return []
    df["period"] = df["start_utc"].map(lambda ts: _period_start(ts, granularity))
    keys = ["period", "provider_name"] if split_by_provider else ["period"]
    grouped = df.groupby(keys)[metric].agg(["sum", "count"]).reset_index()
    out: list[dict[str, Any]] = []
    for _, row in grouped.sort_values(keys).iterrows():
        start: datetime = row["period"]
        item: dict[str, Any] = {
            "period_start": start.isoformat(),
            "period": _label(start, granularity),
            "cost_usd": round(float(row["sum"]), 2),
            "records": int(row["count"]),
        }
        if split_by_provider:
            item["provider"] = str(row["provider_name"])
        out.append(item)
    return out


def _dimension_value(rec: FocusRecord, dimension: str) -> str:
    if dimension == "provider":
        return rec.provider_name.value
    if dimension == "account":
        acct = rec.sub_account_id.strip()
        return UNATTRIBUTED if acct.lower() in PLACEHOLDER_ACCOUNTS else acct
    if dimension == "service":
        return rec.service_name
    if dimension == "category":
        return rec.service_category
    if dimension == "region":
        return rec.region_id
    if dimension == "resource":
        return rec.resource_id or UNATTRIBUTED
    if dimension == "workload":
        workload = rec.tags.get("k8s_workload")
        if rec.tags.get("attribution", "").startswith("unattributed"):
            return UNATTRIBUTED
        if workload:
            cluster = rec.tags.get("k8s_cluster", "")
            return f"{cluster}/{workload}" if cluster else workload
        return NOT_K8S
    if dimension == "charge_category":
        return rec.charge_category.value
    raise ValueError(f"unsupported dimension {dimension!r}; choose one of {DIMENSIONS}")


def spend_breakdown(
    records: list[FocusRecord],
    dimension: str = "service",
    metric: Metric = "billed_cost",
    top_n: int | None = None,
    conflicting_resource_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Cost per dimension value, largest first, with share of total and flags."""
    if dimension not in DIMENSIONS:
        raise ValueError(f"unsupported dimension {dimension!r}; choose one of {DIMENSIONS}")
    conflicts = conflicting_resource_ids or set()
    totals: dict[str, float] = {}
    gross: dict[str, float] = {}
    counts: dict[str, int] = {}
    flagged: dict[str, set[str]] = {}
    for rec in records:
        key = _dimension_value(rec, dimension)
        cost = float(getattr(rec, metric))
        totals[key] = totals.get(key, 0.0) + cost
        gross[key] = gross.get(key, 0.0) + abs(cost)
        counts[key] = counts.get(key, 0) + 1
        if rec.resource_id and rec.resource_id in conflicts:
            flagged.setdefault(key, set()).add("conflicting_attribution")
        if key == UNATTRIBUTED:
            flagged.setdefault(key, set()).add("unattributed")
    grand_total = sum(totals.values())
    # Shares are of GROSS spend (sum of absolute per-record costs): with credits and
    # refunds the net total can be tiny or negative, which makes net-based
    # percentages meaningless. Same basis as the ingestion attribution share.
    gross_total = sum(gross.values())
    ranked = sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))
    items: list[dict[str, Any]] = [
        {
            "key": key,
            "cost_usd": round(value, 2),
            "share": round(gross[key] / gross_total, 4) if gross_total else None,
            "records": counts[key],
            "flags": sorted(flagged.get(key, set())),
        }
        for key, value in ranked
    ]
    other: dict[str, Any] | None = None
    if top_n is not None and len(ranked) > top_n:
        rest = ranked[top_n:]
        items = items[:top_n]
        other = {
            "key": "(other)",
            "cost_usd": round(sum(value for _, value in rest), 2),
            "records": sum(counts[key] for key, _ in rest),
            "members": len(rest),
        }
    unattributed_cost = totals.get(UNATTRIBUTED, 0.0)
    return {
        "dimension": dimension,
        "metric": metric,
        "total_cost_usd": round(grand_total, 2),
        "items": items,
        "other": other,
        "unattributed_cost_usd": round(unattributed_cost, 2),
        "unattributed_share": round(gross.get(UNATTRIBUTED, 0.0) / gross_total, 4) if gross_total else None,
        "share_basis": "gross spend (sum of absolute per-record costs)",
    }


def period_comparison(records: list[FocusRecord], window_days: int, metric: Metric = "billed_cost") -> dict[str, Any]:
    """Compare the latest ``window_days`` with the window immediately before it."""
    if window_days < 1:
        raise ValueError("window_days must be >= 1")
    if not records:
        return {"window_days": window_days, "current_usd": 0.0, "previous_usd": 0.0, "change_pct": None}
    latest = max(r.billing_period_start for r in records)
    latest_day = datetime(latest.year, latest.month, latest.day, tzinfo=timezone.utc)
    # Never compare against a "previous" window that starts before the data does: a half-empty
    # window would show a large, meaningless increase. Shrink both windows to fit instead.
    earliest = min(r.billing_period_start for r in records).astimezone(timezone.utc).date()
    requested = window_days
    fit = ((latest_day.date() - earliest).days + 1) // 2
    if 1 <= fit < window_days:
        window_days = fit
    current_start = latest_day - timedelta(days=window_days - 1)
    previous_start = current_start - timedelta(days=window_days)

    def _sum(lo: datetime, hi: datetime) -> float:
        return sum(
            float(getattr(r, metric)) for r in records if lo <= r.billing_period_start.astimezone(timezone.utc) < hi
        )

    current = _sum(current_start, latest_day + timedelta(days=1))
    previous = _sum(previous_start, current_start)
    return {
        "window_days": window_days,
        "window_shortened_to_fit_data": window_days != requested,
        "current_window_start": current_start.date().isoformat(),
        "previous_window_start": previous_start.date().isoformat(),
        "current_usd": round(current, 2),
        "previous_usd": round(previous, 2),
        "change_usd": round(current - previous, 2),
        "change_pct": round(100.0 * (current - previous) / previous, 2) if previous else None,
    }


def spend_overview(records: list[FocusRecord], metric: Metric = "billed_cost") -> dict[str, Any]:
    """Headline numbers for the dashboard home view."""
    if not records:
        return {"total_cost_usd": 0.0, "records": 0, "period_start": None, "period_end": None, "by_charge_category": {}}
    by_cat: dict[str, float] = {}
    for r in records:
        by_cat[r.charge_category.value] = by_cat.get(r.charge_category.value, 0.0) + float(getattr(r, metric))
    days = {r.billing_period_start.astimezone(timezone.utc).date() for r in records}
    total = sum(by_cat.values())
    return {
        "total_cost_usd": round(total, 2),
        "records": len(records),
        "days_covered": len(days),
        "average_daily_cost_usd": round(total / len(days), 2) if days else 0.0,
        "period_start": min(days).isoformat(),
        "period_end": max(days).isoformat(),
        "by_charge_category": {k: round(v, 2) for k, v in sorted(by_cat.items())},
    }
