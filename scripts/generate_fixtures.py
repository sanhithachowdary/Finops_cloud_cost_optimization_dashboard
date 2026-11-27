"""Generate the versioned, SYNTHETIC benchmark fixtures used by tests and the KPI harness.

    python -m scripts.generate_fixtures            # (re)writes tests/fixtures/*_v1.*

Deliberately uses only the Python standard library and does not import
``finops_engine``: the ground-truth labels are produced by the injection plan
below, independently of the detector that is later evaluated against them.
Output is byte-for-byte deterministic for a given GENERATOR_VERSION.
"""

from __future__ import annotations

import csv
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

GENERATOR_VERSION = "benchmark-v1"
SEED = 20260630
DAYS = 120
END = datetime(2026, 7, 1, tzinfo=timezone.utc)  # exclusive: last day is 2026-06-30 (end of Q2)
FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"

# provider, service, account, region, base USD/day, resource id, weekday-seasonal?
SERIES = [
    (
        "AWS",
        "AmazonEC2",
        "111111111111",
        "us-east-1",
        420.0,
        "arn:aws:ec2:us-east-1:111111111111:instance/i-0web01",
        True,
    ),
    ("AWS", "AmazonRDS", "111111111111", "us-east-1", 260.0, "arn:aws:rds:us-east-1:111111111111:db/orders", False),
    ("AWS", "AmazonS3", "111111111111", "us-east-1", 95.0, "arn:aws:s3:::finops-data-lake", False),
    ("AWS", "AmazonEKS", "222222222222", "us-west-2", 310.0, "arn:aws:eks:us-west-2:222222222222:cluster/core", True),
    ("GCP", "Compute Engine", "analytics-prod", "us-central1", 380.0, "projects/analytics-prod/instances/etl-01", True),
    ("GCP", "BigQuery", "analytics-prod", "us-central1", 210.0, "projects/analytics-prod/datasets/warehouse", False),
    ("GCP", "Cloud Storage", "analytics-prod", "us-central1", 70.0, "projects/analytics-prod/disks/pd-orphan-3", False),
    ("GCP", "GKE", "platform-prod", "europe-west1", 290.0, "projects/platform-prod/clusters/gke-main", True),
    ("Azure", "Virtual Machines", "sub-0001", "eastus", 350.0, "/subscriptions/sub-0001/vm/api-vm-01", True),
    ("Azure", "SQL Database", "sub-0001", "eastus", 230.0, "/subscriptions/sub-0001/sql/billing-db", False),
    ("Azure", "Blob Storage", "sub-0001", "eastus", 60.0, "/subscriptions/sub-0001/storage/archive", False),
    ("Azure", "AKS", "sub-0002", "westeurope", 270.0, "/subscriptions/sub-0002/aks/aks-main", True),
    (
        "AWS",
        "AmazonEC2",
        "111111111111",
        "us-east-1",
        48.0,
        "arn:aws:ec2:us-east-1:111111111111:instance/i-0legacy07",
        False,
    ),
]

# day index (0-based from start), provider, service, multiplier  -> ground truth.
INJECTIONS = [
    (40, "AWS", "AmazonEC2", 3.5),
    (41, "AWS", "AmazonEC2", 3.2),
    (55, "GCP", "BigQuery", 4.0),
    (63, "Azure", "Virtual Machines", 2.8),
    (77, "AWS", "AmazonRDS", 2.6),
    (84, "GCP", "GKE", 3.0),
    (92, "Azure", "SQL Database", 2.5),
    (101, "AWS", "AmazonEKS", 2.9),
    (110, "GCP", "Compute Engine", 1.45),  # subtle: may legitimately be missed (recall < 1)
    (115, "Azure", "AKS", 1.5),  # subtle
]

# Independent HOLD-OUT set: different seed and injection plan.  Never used to
# choose detector thresholds; evaluated once with the shipped configuration.
HOLDOUT_SEED = 7
HOLDOUT_INJECTIONS = [
    (33, "Azure", "AKS", 3.1),
    (47, "AWS", "AmazonS3", 2.4),
    (52, "GCP", "Compute Engine", 2.7),
    (53, "GCP", "Compute Engine", 2.2),
    (68, "AWS", "AmazonEC2", 3.8),
    (81, "Azure", "Blob Storage", 2.9),
    (95, "GCP", "BigQuery", 1.6),
    (108, "AWS", "AmazonRDS", 2.0),
    (117, "Azure", "Virtual Machines", 1.35),  # subtle
]


def _cost(rng: random.Random, base: float, day: int, date: datetime, seasonal: bool) -> float:
    trend = 1.0 + 0.0015 * day  # +0.15 %/day growth
    weekly = 0.88 if seasonal and date.weekday() >= 5 else 1.0
    noise = rng.gauss(1.0, 0.05)
    return max(0.01, base * trend * weekly * noise)


def generate_benchmark(
    seed: int = SEED,
    injections: list[tuple[int, str, str, float]] = INJECTIONS,
    billing_name: str = "billing_benchmark_v1.csv",
    labels_name: str = "anomaly_labels_v1.json",
) -> None:
    rng = random.Random(seed)
    start = END - timedelta(days=DAYS)
    injected = {(d, p, s): m for d, p, s, m in injections}
    rows = []
    for day in range(DAYS):
        date = start + timedelta(days=day)
        for provider, service, account, region, base, resource, seasonal in SERIES:
            cost = _cost(rng, base, day, date, seasonal)
            if not resource.endswith("i-0legacy07"):
                cost *= injected.get((day, provider, service), 1.0)
            rows.append(
                {
                    "ProviderName": provider,
                    "PublisherName": provider,
                    "ChargeCategory": "Usage",
                    "BilledCost": f"{cost:.4f}",
                    "EffectiveCost": f"{cost * 0.95:.4f}",
                    "BillingCurrency": "USD",
                    "ConsumedQuantity": "24",
                    "ConsumedUnit": "Hours",
                    "ServiceName": service,
                    "RegionId": region,
                    "SubAccountId": account,
                    "ResourceId": resource,
                    "ChargePeriodStart": date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "ChargePeriodEnd": (date + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "Tags": "env=prod" if "api-vm-01" in resource else "",
                }
            )
    FIXTURES.mkdir(parents=True, exist_ok=True)
    with (FIXTURES / billing_name).open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    labels = [
        {"date": (start + timedelta(days=d)).strftime("%Y-%m-%d"), "provider": p, "service": s, "multiplier": m}
        for d, p, s, m in injections
    ]
    meta = {
        "generator_version": GENERATOR_VERSION,
        "billing_fixture": billing_name,
        "seed": seed,
        "days": DAYS,
        "period": [start.strftime("%Y-%m-%d"), (END - timedelta(days=1)).strftime("%Y-%m-%d")],
        "label_semantics": "a (date, provider, service) day whose spend was multiplied by `multiplier`",
        "labels": labels,
        "notes": "SYNTHETIC data. Labels come from the injection plan, not from any detector.",
    }
    (FIXTURES / labels_name).write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")


def generate_utilization() -> None:
    """Utilisation for the benchmark resources with the *expected* outcome per
    resource, derived by hand from the documented policy (docs/api-and-data-contracts.md)."""
    samples = [
        # over-provisioned VM -> rightsizing actionable
        {
            "resource_id": SERIES[0][5],
            "provider": "AWS",
            "resource_type": "vm",
            "size": "m5.4xlarge",
            "cpu_avg_pct": 14.0,
            "cpu_p95_pct": 24.0,
            "mem_avg_pct": 22.0,
            "mem_p95_pct": 33.0,
            "observation_days": 30,
            "expected": {"rightsizing": "actionable"},
        },
        # healthy database -> no recommendation
        {
            "resource_id": SERIES[1][5],
            "provider": "AWS",
            "resource_type": "database",
            "size": "db.r5.2xlarge",
            "cpu_avg_pct": 48.0,
            "cpu_p95_pct": 71.0,
            "mem_avg_pct": 60.0,
            "mem_p95_pct": 78.0,
            "observation_days": 30,
            "expected": {"rightsizing": None},
        },
        # k8s workload over-requested -> actionable
        {
            "resource_id": SERIES[3][5],
            "provider": "AWS",
            "resource_type": "k8s_workload",
            "size": "8 vCPU requested",
            "cpu_avg_pct": 11.0,
            "cpu_p95_pct": 19.0,
            "mem_avg_pct": 25.0,
            "mem_p95_pct": 36.0,
            "observation_days": 30,
            "expected": {"rightsizing": "actionable"},
        },
        # VM with only 5 days of telemetry -> requires_review
        {
            "resource_id": SERIES[4][5],
            "provider": "GCP",
            "resource_type": "vm",
            "size": "n2-standard-8",
            "cpu_avg_pct": 9.0,
            "cpu_p95_pct": 15.0,
            "mem_avg_pct": 20.0,
            "mem_p95_pct": 28.0,
            "observation_days": 5,
            "expected": {"rightsizing": "requires_review"},
        },
        # orphan disk -> idle actionable
        {
            "resource_id": SERIES[6][5],
            "provider": "GCP",
            "resource_type": "disk",
            "size": "2 TB pd-standard",
            "attached": False,
            "observation_days": 30,
            "expected": {"idle": "actionable"},
        },
        # protected prod VM, low utilisation -> requires_review (protected tag)
        {
            "resource_id": SERIES[8][5],
            "provider": "Azure",
            "resource_type": "vm",
            "size": "Standard_D8s_v5",
            "cpu_avg_pct": 15.0,
            "cpu_p95_pct": 26.0,
            "mem_avg_pct": 25.0,
            "mem_p95_pct": 37.0,
            "observation_days": 30,
            "tags": {"env": "prod"},
            "expected": {"rightsizing": "requires_review"},
        },
        # database without CPU telemetry -> insufficient_data
        {
            "resource_id": SERIES[9][5],
            "provider": "Azure",
            "resource_type": "database",
            "size": "GP_Gen5_8",
            "observation_days": 30,
            "expected": {"rightsizing": "insufficient_data"},
        },
        # idle legacy instance -> idle actionable (terminate path), rightsizing suppressed by overlap control
        {
            "resource_id": SERIES[12][5],
            "provider": "AWS",
            "resource_type": "vm",
            "size": "t3.large",
            "cpu_avg_pct": 0.4,
            "cpu_p95_pct": 1.1,
            "mem_avg_pct": 6.0,
            "mem_p95_pct": 8.0,
            "requests_per_day": 0.0,
            "network_mb_per_day": 0.3,
            "observation_days": 30,
            "expected": {"idle": "actionable", "rightsizing": "suppressed"},
        },
    ]
    payload = {
        "generator_version": GENERATOR_VERSION,
        "notes": "SYNTHETIC utilisation. `expected` is a hand-derived oracle and is stripped before ingestion.",
        "samples": samples,
    }
    (FIXTURES / "utilization_benchmark_v1.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    generate_benchmark()
    generate_benchmark(HOLDOUT_SEED, HOLDOUT_INJECTIONS, "billing_holdout_v1.csv", "anomaly_labels_holdout_v1.json")
    generate_utilization()
    print(f"Fixtures written to {FIXTURES} ({GENERATOR_VERSION})")
