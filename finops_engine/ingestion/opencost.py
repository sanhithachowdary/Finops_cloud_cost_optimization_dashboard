"""OpenCost allocation adapter (FR-1, Kubernetes workload attribution).

Parses the JSON returned by the OpenCost ``/allocation`` API, i.e.
``{"code": 200, "data": [ {"<name>": Allocation, ...}, ... ]}`` (one map per
``step``), using the field names defined in OpenCost's
``core/pkg/opencost/allocation.go`` (``cpuCost``, ``ramCost``, ``totalCost``,
``cpuCoreRequestAverage``, ``cpuCoreUsageAverage``, ``ramByteRequestAverage``,
``ramByteUsageAverage``, ``properties.cluster/namespace/controller`` …).

The adapter does **not** call a live OpenCost server; it converts an exported
response (file or API body) into canonical billing rows that then pass through
the same validator as every other source, plus utilisation samples for
right-sizing.  Tested only against fixtures (see ``tests/fixtures``).
"""

from __future__ import annotations

from typing import Any

from finops_engine.optimization.models import UtilizationSample

# OpenCost aggregates cost it cannot attribute to a workload under these names.
UNATTRIBUTED_NAMES = {"__idle__", "__unallocated__", "__unmounted__"}
COST_COMPONENTS = (
    "cpuCost",
    "gpuCost",
    "ramCost",
    "pvCost",
    "networkCost",
    "loadBalancerCost",
    "sharedCost",
    "externalCost",
)
PROVIDER_ID_PREFIXES = {"aws://": "AWS", "gce://": "GCP", "azure://": "Azure"}


def _provider_from_props(props: dict[str, Any], default: str | None) -> str | None:
    provider_id = str(props.get("providerID") or "")
    for prefix, provider in PROVIDER_ID_PREFIXES.items():
        if provider_id.startswith(prefix):
            return provider
    return default


def _allocation_cost(alloc: dict[str, Any]) -> Any:
    if "totalCost" in alloc:
        return alloc["totalCost"]
    parts: list[Any] = [alloc[k] for k in COST_COMPONENTS if alloc.get(k) is not None]
    if not parts:
        return None
    try:
        return float(sum(float(p) for p in parts))
    except (TypeError, ValueError):
        return "malformed"


def iter_allocations(payload: Any) -> list[tuple[str, dict[str, Any]]]:
    if not isinstance(payload, dict) or "data" not in payload:
        raise ValueError("OpenCost payload must be an object with a 'data' field")
    code = payload.get("code", 200)
    if code != 200:
        raise ValueError(f"OpenCost response code {code} is not 200")
    data = payload["data"]
    sets = data if isinstance(data, list) else [data]
    out: list[tuple[str, dict[str, Any]]] = []
    for allocation_set in sets:
        if allocation_set is None:
            continue
        if not isinstance(allocation_set, dict):
            raise ValueError("each OpenCost allocation set must be an object")
        for name, alloc in allocation_set.items():
            if not isinstance(alloc, dict):
                raise ValueError(f"allocation {name!r} is not an object")
            out.append((str(name), alloc))
    return out


def opencost_to_rows(
    payload: Any,
    *,
    default_provider: str | None = None,
    currency: str = "USD",
    account_id: str | None = None,
) -> tuple[list[dict[str, Any]], list[UtilizationSample]]:
    """Convert an OpenCost allocation response into canonical rows + utilisation."""
    rows: list[dict[str, Any]] = []
    samples: list[UtilizationSample] = []
    for name, alloc in iter_allocations(payload):
        props = alloc.get("properties") or {}
        if not isinstance(props, dict):
            props = {}
        cluster = str(props.get("cluster") or "")
        namespace = str(props.get("namespace") or "")
        controller = str(props.get("controller") or "")
        kind = str(props.get("controllerKind") or "")
        unattributed = name in UNATTRIBUTED_NAMES or not namespace
        workload = f"{namespace}/{kind}/{controller}" if controller else (namespace or name)
        resource_id = None if unattributed else f"k8s://{cluster or 'cluster'}/{workload}"
        tags = {"k8s_cluster": cluster, "k8s_namespace": namespace, "k8s_workload": workload, "source": "opencost"}
        labels = props.get("labels")
        if isinstance(labels, dict):
            for key in ("team", "owner", "env", "app", "do-not-delete", "finops-exempt"):
                if key in labels:
                    tags[key] = str(labels[key])
        if unattributed:
            tags["attribution"] = "unattributed:" + name
        provider = _provider_from_props(props, default_provider)
        rows.append(
            {
                "provider_name": provider,
                "publisher_name": "OpenCost",
                "charge_category": "Usage",
                "billed_cost": _allocation_cost(alloc),
                "effective_cost": _allocation_cost(alloc),
                "currency": currency,
                "usage_quantity": alloc.get("cpuCoreHours", 0.0),
                "usage_unit": "vCPU-Hours",
                "service_name": "Kubernetes",
                "service_category": "Container",
                "region_id": str(props.get("region") or "global"),
                "sub_account_id": account_id or (cluster or "unknown"),
                "resource_id": resource_id,
                "billing_period_start": alloc.get("start") or (alloc.get("window") or {}).get("start"),
                "billing_period_end": alloc.get("end") or (alloc.get("window") or {}).get("end"),
                "tags": tags,
            }
        )
        if resource_id is None:
            continue
        sample = _utilization_from_alloc(resource_id, provider, alloc, tags)
        if sample is not None:
            samples.append(sample)
    return rows, samples


def _ratio_pct(usage: Any, request: Any) -> float | None:
    try:
        u = float(usage)
        r = float(request)
    except (TypeError, ValueError):
        return None
    if r <= 0 or u < 0:
        return None
    return round(min(100.0, 100.0 * u / r), 2)


def _utilization_from_alloc(
    resource_id: str, provider: str | None, alloc: dict[str, Any], tags: dict[str, str]
) -> UtilizationSample | None:
    cpu = _ratio_pct(alloc.get("cpuCoreUsageAverage"), alloc.get("cpuCoreRequestAverage"))
    mem = _ratio_pct(alloc.get("ramByteUsageAverage"), alloc.get("ramByteRequestAverage"))
    if cpu is None and mem is None:
        return None
    try:
        hours = float(alloc.get("minutes", 0.0)) / 60.0
    except (TypeError, ValueError):
        hours = 0.0
    return UtilizationSample(
        resource_id=resource_id,
        provider=provider,
        resource_type="k8s_workload",
        size=f"{alloc.get('cpuCoreRequestAverage', '?')} vCPU requested",
        # OpenCost exposes averages, not percentiles: the average is used as a
        # p95 proxy and the sample is flagged so confidence is reduced.
        cpu_avg_pct=cpu,
        cpu_p95_pct=cpu,
        mem_avg_pct=mem,
        mem_p95_pct=mem,
        observation_days=round(hours / 24.0, 2) if hours else None,
        percentiles_are_proxies=True,
        tags=tags,
        source="opencost",
    )
