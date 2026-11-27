"""Bills for any date range from one simulated company (GENERATED data).

The company's 17 cloud resources are listed in data/sample-estate.csv. A resource's cost and CPU
on a given day depend only on the seed, the resource and the date, so two bills that overlap
contain identical rows (same line Id) for the shared days and the engine counts them once.
Planted problems have fixed dates and are written to a .labels.json file next to each bill.
"""

from __future__ import annotations

import csv
import zlib
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ESTATE_FILE = Path(__file__).resolve().parents[1] / "data" / "sample-estate.csv"
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
EPOCH = date(2026, 1, 1)  # organic growth is counted from this day


@dataclass(frozen=True)
class Resource:
    provider: str
    service: str
    resource_id: str | None
    account: str | None
    region: str
    usage_type: str
    unit: str
    price_usd: float
    qty_per_day: float
    kind: str  # compute | database | storage | serverless | analytics | network
    cpu_mean: float | None  # weekday CPU %; None = no CPU signal
    noise: float  # day-to-day usage noise
    growth_per_day: float
    tags: str  # "k=v;k2=v2"


def load_estate(path: Path = ESTATE_FILE) -> tuple[Resource, ...]:
    with path.open(newline="", encoding="utf-8") as f:
        return tuple(
            Resource(
                provider=r["provider"],
                service=r["service"],
                resource_id=r["resource_id"] or None,
                account=r["account"] or None,
                region=r["region"],
                usage_type=r["usage_type"],
                unit=r["unit"],
                price_usd=float(r["price_usd"]),
                qty_per_day=float(r["qty_per_day"]),
                kind=r["kind"],
                cpu_mean=float(r["cpu_mean"]) if r["cpu_mean"] else None,
                noise=float(r["noise"]),
                growth_per_day=float(r["growth_per_day"]),
                tags=r["tags"],
            )
            for r in csv.DictReader(f)
        )


ESTATE = load_estate()


@dataclass(frozen=True)
class Event:
    kind: str  # spike | step | drift | idle
    resource_id: str
    start: date
    end: date | None = None  # inclusive; None = lasts to the end of any bill
    factor: float = 1.0  # cost multiplier (spike/step) or extra growth per day (drift)
    note: str = ""

    def active(self, day: date) -> bool:
        return self.start <= day and (self.end is None or day <= self.end)


DEMO_EVENTS = (
    Event("spike", "fn-image-resize", date(2026, 2, 20), date(2026, 2, 20), 5.0, "runaway retry loop"),
    Event("drift", "s3-media-assets", date(2026, 3, 1), date(2026, 5, 31), 0.01, "logs never cleaned up"),
    Event("idle", "vm-reporting-02", date(2026, 5, 1), None, 0.0, "report job retired, VM left running"),
    Event("step", "gke-reco-pool", date(2026, 6, 10), None, 2.0, "node pool doubled overnight"),
    Event("spike", "func-invoice-pdf", date(2026, 8, 14), date(2026, 8, 14), 4.0, "month-end batch reran 4x"),
    Event("spike", "bq-analytics", date(2026, 11, 3), date(2026, 11, 3), 6.0, "unbounded query"),
)
PRESETS: dict[str, tuple[Event, ...]] = {"demo": DEMO_EVENTS, "none": ()}


def parse_period(text: str, year: int) -> tuple[date, date]:
    """'jan-mar' -> 1 Jan..31 Mar; 'apr' -> April; 'nov-feb' runs into the next year."""
    parts = [p.strip().lower()[:3] for p in text.split("-")]
    if not 1 <= len(parts) <= 2 or any(p not in MONTHS for p in parts):
        raise ValueError(f"period must look like 'jan-mar' or 'apr', got {text!r}")
    first, last = MONTHS.index(parts[0]) + 1, MONTHS.index(parts[-1]) + 1
    end_year = year if last >= first else year + 1
    end = (pd.Timestamp(end_year, last, 1) + pd.offsets.MonthEnd(0)).date()
    return date(year, first, 1), end


def bill_row(res: Resource, day: date, seed: int, events: tuple[Event, ...], fx: float, currency: str) -> dict:
    key = zlib.crc32(f"{res.provider}|{res.service}|{res.resource_id}".encode())
    rng = np.random.default_rng([seed, key, day.toordinal()])

    qty = res.qty_per_day * (1 + res.growth_per_day * (day - EPOCH).days)
    if res.kind not in ("compute", "database"):  # always-on machines bill a fixed 24 h a day
        qty *= max(0.0, 1 + rng.normal(0, res.noise))
    cpu = None
    if res.cpu_mean is not None:
        cpu = res.cpu_mean * (0.7 if day.weekday() >= 5 else 1.0) + rng.normal(0, 6)

    for ev in events:
        if ev.resource_id != res.resource_id or not ev.active(day):
            continue
        if ev.kind in ("spike", "step"):
            qty *= ev.factor
            if cpu is not None:
                cpu = min(97.0, cpu * 1.3)
        elif ev.kind == "drift":
            qty *= 1 + ev.factor * ((day - ev.start).days + 1)
        elif ev.kind == "idle" and cpu is not None:
            cpu = 1.0 + abs(rng.normal(0, 0.5))

    price = res.price_usd * fx
    cost = round(qty * price, 4)
    return {
        "Id": f"{res.resource_id or 'shared'}-{day.isoformat()}",
        "ChargePeriodStart": f"{day.isoformat()}T00:00:00Z",
        "ChargePeriodEnd": f"{(day + timedelta(days=1)).isoformat()}T00:00:00Z",
        "ProviderName": res.provider,
        "ServiceName": res.service,
        "ChargeCategory": "Usage",
        "ResourceId": res.resource_id or "",
        "SubAccountId": res.account or "",
        "RegionId": res.region,
        "UsageType": res.usage_type,
        "UsageQuantity": round(qty, 4),
        "UsageUnit": res.unit,
        "ContractedUnitPrice": round(price, 8),
        "BilledCost": cost,
        "EffectiveCost": cost,
        "BillingCurrency": currency,
        "Tags": res.tags,
        "ResourceUtilizationPercent": "" if cpu is None else round(min(100.0, max(0.0, cpu)), 2),
    }


def planted_labels(events: tuple[Event, ...], start: date, end: date) -> list[dict[str, Any]]:
    """Ground truth: every day of a spike, and only the first day of a step (the new level persists)."""
    by_id = {r.resource_id: r for r in ESTATE}
    labels = []
    for ev in events:
        if ev.kind not in ("spike", "step"):
            continue
        last = ev.start if ev.kind == "step" else (ev.end or ev.start)
        day = ev.start
        while day <= last:
            if start <= day <= end:
                res = by_id[ev.resource_id]
                labels.append({"date": day.isoformat(), "provider": res.provider, "service": res.service,
                               "kind": ev.kind, "multiplier": ev.factor, "note": ev.note})  # fmt: skip
            day += timedelta(days=1)
    return labels


def generate_period(
    start: date,
    end: date,
    *,
    currency: str = "USD",
    fx: float = 1.0,
    seed: int = 2026,
    events: tuple[Event, ...] = DEMO_EVENTS,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Return (bill, labels metadata) for start..end inclusive."""
    if end < start:
        raise ValueError("end date is before start date")
    if (end - start).days > 3 * 366:
        raise ValueError("at most 3 years per bill")
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    rows = [bill_row(res, day, seed, events, fx, currency) for day in days for res in ESTATE]
    other = [
        {
            "kind": ev.kind,
            "resource_id": ev.resource_id,
            "start": ev.start.isoformat(),
            "end": ev.end.isoformat() if ev.end else None,
            "note": ev.note,
        }  # fmt: skip
        for ev in events
        if ev.kind in ("drift", "idle") and ev.start <= end and (ev.end is None or ev.end >= start)
    ]
    meta = {
        "generator_version": "period-bills-v1",
        "seed": seed,
        "period": [start.isoformat(), end.isoformat()],
        "currency": currency,
        "label_semantics": "(date, provider, service) days with a planted spike, or the first day of a planted step",
        "other_planted_events": other,
        "labels": planted_labels(events, start, end),
        "data_label": "GENERATED sample bill, not a real cloud bill",
    }
    return pd.DataFrame(rows), meta
