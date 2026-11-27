"""Derive utilisation samples from a per-row utilisation column in a billing CSV.

FOCUS billing exports carry no utilisation, so the engine takes it separately
(``POST /api/v1/ingest/utilization``). Some demo and internal exports add a column such as
``ResourceUtilizationPercent`` to every billing row; this module turns that column into
:class:`UtilizationSample` objects, one per resource.

Assumptions (recorded on every sample, never hidden):

* The column is **CPU** utilisation in percent (0-100). Memory is not measured, so it stays
  ``None``, which makes right-sizing ``requires_review`` rather than ``actionable``.
* Values are one per billing row (typically daily), so the p95 is a p95 of *daily* values,
  not of intra-day samples: ``percentiles_are_proxies=True`` lowers confidence.
* Only compute-like and database resources are converted. Storage and serverless utilisation
  percentages say nothing about right-sizing or idleness (disks need attachment state), so they
  are skipped with a reason instead of producing misleading recommendations.
"""

from __future__ import annotations

import contextlib
import math
from collections import defaultdict
from typing import Any

from finops_engine.ingestion.validator import _clean, _parse_ts
from finops_engine.optimization.models import UtilizationSample

DEFAULT_COLUMN = "ResourceUtilizationPercent"
SOURCE_PREFIX = "billing_column"

_DATABASE_HINTS = ("database", "sql", "rds", "db")
_COMPUTE_HINTS = ("compute", "ec2", "virtual machine", "vm", "instance", "gke", "aks", "eks")
_SKIP_HINTS = ("storage", "s3", "blob", "lambda", "function", "serverless")


def infer_resource_type(service: str, usage_type: str = "") -> str | None:
    """Map a service/usage-type name to a supported resource type, or None to skip."""
    text = f"{service} {usage_type}".lower()
    if any(h in text for h in _SKIP_HINTS):
        return None
    if any(h in text for h in _DATABASE_HINTS):
        return "database"
    if any(h in text for h in _COMPUTE_HINTS):
        return "vm"
    return None


def _p95(values: list[float]) -> float:
    """Nearest-rank 95th percentile."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def samples_from_billing_rows(
    rows: list[dict[str, Any]], column: str = DEFAULT_COLUMN
) -> tuple[list[UtilizationSample], list[dict[str, Any]]]:
    """Return ``(samples, skipped)``. ``skipped`` lists resources not converted, with a reason."""
    values: dict[str, list[float]] = defaultdict(list)
    days: dict[str, set[str]] = defaultdict(set)
    meta: dict[str, dict[str, str]] = {}
    invalid: dict[str, int] = defaultdict(int)

    for row in rows:
        resource_id = _clean(row.get("ResourceId") or row.get("resource_id"))
        if not resource_id:
            continue
        meta.setdefault(
            resource_id,
            {
                "provider": _clean(row.get("ProviderName") or row.get("provider_name")) or "",
                "service": _clean(row.get("ServiceName") or row.get("service_name")) or "",
                "usage_type": _clean(row.get("UsageType")) or "",
            },
        )
        raw = _clean(row.get(column))
        try:
            pct = float(raw) if raw is not None else math.nan
        except ValueError:
            pct = math.nan
        if not math.isfinite(pct) or not 0 <= pct <= 100:
            invalid[resource_id] += 1
            continue
        values[resource_id].append(pct)
        start = _clean(row.get("ChargePeriodStart") or row.get("billing_period_start"))
        if start:
            with contextlib.suppress(ValueError):
                days[resource_id].add(_parse_ts(start)[0].date().isoformat())

    samples: list[UtilizationSample] = []
    skipped: list[dict[str, Any]] = []
    for resource_id in sorted(meta):
        info = meta[resource_id]
        rtype = infer_resource_type(info["service"], info["usage_type"])
        if rtype is None:
            skipped.append(
                {
                    "resource_id": resource_id,
                    "service": info["service"],
                    "reason": "not compute/database: utilisation % is not a right-sizing or idle signal",
                }
            )
            continue
        observed = values.get(resource_id, [])
        if not observed:
            skipped.append(
                {
                    "resource_id": resource_id,
                    "service": info["service"],
                    "reason": f"no valid {column} values (0-100)",
                }
            )
            continue
        samples.append(
            UtilizationSample(
                resource_id=resource_id,
                provider=info["provider"] or None,
                resource_type=rtype,
                cpu_avg_pct=round(sum(observed) / len(observed), 2),
                cpu_p95_pct=round(_p95(observed), 2),
                observation_days=float(len(days[resource_id]) or len(observed)),
                percentiles_are_proxies=True,
                source=f"{SOURCE_PREFIX}:{column}"
                + (f" ({invalid[resource_id]} invalid values ignored)" if invalid.get(resource_id) else ""),
            )
        )
    return samples, skipped
