"""Deterministic synthetic cost telemetry generator.

Emits FOCUS records with embedded anomalies and stable resource identities
across the observation window.
"""

import random
from datetime import datetime, timedelta, timezone

from finops_engine.schema.focus_spec import ChargeCategory, CloudProvider, FocusRecord, categorize_service


class MockTelemetryConnector:
    """Generates deterministic mock telemetry with stable resource identities."""

    def __init__(
        self,
        days: int = 90,
        seed: int = 42,
        inject_anomalies: bool = True,
        end_date: datetime | None = None,
    ) -> None:
        self.days = days
        self.seed = seed
        self._rng = random.Random(seed)
        self.inject_anomalies = inject_anomalies
        # A fixed end_date makes the output fully reproducible (fixtures, KPI
        # harness); the default keeps the original "ends now" behaviour.
        self.end_date = end_date

    def fetch_cost_data(self) -> list[FocusRecord]:
        records: list[FocusRecord] = []
        end_date = self.end_date or datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=self.days)

        providers = [
            (CloudProvider.AWS, ["AmazonEC2", "AmazonS3", "AmazonRDS", "AmazonEKS"], "aws-prod-account"),
            (CloudProvider.GCP, ["Compute Engine", "Cloud Storage", "BigQuery", "GKE"], "gcp-analytics-project"),
            (CloudProvider.AZURE, ["Virtual Machines", "Blob Storage", "SQL Database", "AKS"], "azure-enterprise-sub"),
        ]

        current_dt = start_date
        day_counter = 0

        while current_dt <= end_date:
            day_counter += 1
            next_dt = current_dt + timedelta(days=1)

            for provider, services, sub_acc in providers:
                for service in services:
                    base_cost = self._rng.uniform(80.0, 300.0)

                    # Inject realistic cost anomalies on specific days when enabled
                    if self.inject_anomalies:
                        if provider == CloudProvider.AWS and service == "AmazonEC2" and day_counter in (45, 46, 47):
                            base_cost *= 4.5  # Massive unexpected EC2 spike
                        elif provider == CloudProvider.GCP and service == "BigQuery" and day_counter in (70, 71):
                            base_cost *= 3.8  # Large query spike
                        elif (
                            provider == CloudProvider.AZURE
                            and service == "Virtual Machines"
                            and day_counter in (25, 26)
                        ):
                            base_cost *= 3.0  # Azure VM runaway instance spike

                    effective = base_cost * self._rng.uniform(0.85, 0.98)
                    usage = self._rng.uniform(10.0, 240.0)
                    service_slug = service.lower().replace(" ", "-")
                    resource_id = f"{provider.value.lower()}-{service_slug}-prod-001"

                    records.append(
                        FocusRecord(
                            provider_name=provider,
                            publisher_name=f"{provider.value} Infrastructure",
                            charge_category=ChargeCategory.USAGE,
                            billed_cost=round(base_cost, 2),
                            effective_cost=round(effective, 2),
                            currency="USD",
                            usage_quantity=round(usage, 2),
                            usage_unit="Hours",
                            service_name=service,
                            service_category=categorize_service(service),
                            region_id="us-east-1"
                            if provider == CloudProvider.AWS
                            else ("us-central1" if provider == CloudProvider.GCP else "eastus"),
                            sub_account_id=sub_acc,
                            resource_id=resource_id,
                            billing_period_start=current_dt,
                            billing_period_end=next_dt,
                        )
                    )

            current_dt = next_dt

        return records


# Synthetic utilisation profiles for the 12 mock resources (labelled SYNTHETIC).
# The mix deliberately covers every recommendation outcome so the dashboard can
# demonstrate them: over-provisioned, healthy, idle, missing telemetry and
# protected resources.
_MOCK_UTILIZATION_PROFILES: dict[str, dict[str, object]] = {
    "aws-amazonec2-prod-001": {"resource_type": "vm", "size": "m5.4xlarge", "cpu_p95": 22.0, "mem_p95": 31.0},
    "aws-amazonrds-prod-001": {"resource_type": "database", "size": "db.r5.2xlarge", "cpu_p95": 63.0, "mem_p95": 71.0},
    "aws-amazoneks-prod-001": {
        "resource_type": "k8s_workload",
        "size": "8 vCPU requested",
        "cpu_p95": 18.0,
        "mem_p95": 35.0,
    },
    "aws-amazons3-prod-001": {"resource_type": "other", "size": None, "cpu_p95": None, "mem_p95": None},
    "gcp-compute-engine-prod-001": {
        "resource_type": "vm",
        "size": "n2-standard-8",
        "cpu_p95": 1.2,
        "mem_p95": 4.0,
        "requests": 0.0,
        "network": 0.4,
    },
    "gcp-bigquery-prod-001": {"resource_type": "other", "size": None, "cpu_p95": None, "mem_p95": None},
    "gcp-gke-prod-001": {
        "resource_type": "k8s_workload",
        "size": "16 vCPU requested",
        "cpu_p95": 55.0,
        "mem_p95": 61.0,
    },
    "gcp-cloud-storage-prod-001": {"resource_type": "disk", "size": "2 TB", "attached": False},
    "azure-virtual-machines-prod-001": {
        "resource_type": "vm",
        "size": "Standard_D8s_v5",
        "cpu_p95": 27.0,
        "mem_p95": 38.0,
        "tags": {"env": "prod"},
    },
    "azure-sql-database-prod-001": {"resource_type": "database", "size": "GP_Gen5_8", "cpu_p95": None, "mem_p95": None},
    "azure-aks-prod-001": {
        "resource_type": "k8s_workload",
        "size": "12 vCPU requested",
        "cpu_p95": 34.0,
        "mem_p95": 44.0,
    },
    "azure-blob-storage-prod-001": {"resource_type": "other", "size": None, "cpu_p95": None, "mem_p95": None},
}


def mock_utilization(observation_days: int = 30) -> list:
    """Deterministic SYNTHETIC utilisation samples for the mock resources."""
    from finops_engine.optimization.models import UtilizationSample

    samples = []
    for resource_id, profile in _MOCK_UTILIZATION_PROFILES.items():
        provider = resource_id.split("-", 1)[0].upper().replace("AZURE", "Azure")
        cpu = profile.get("cpu_p95")
        mem = profile.get("mem_p95")
        samples.append(
            UtilizationSample(
                resource_id=resource_id,
                provider=provider,
                resource_type=str(profile["resource_type"]),
                size=profile.get("size"),  # type: ignore[arg-type]
                cpu_p95_pct=cpu,  # type: ignore[arg-type]
                cpu_avg_pct=round(float(cpu) * 0.6, 2) if cpu is not None else None,  # type: ignore[arg-type]
                mem_p95_pct=mem,  # type: ignore[arg-type]
                mem_avg_pct=round(float(mem) * 0.7, 2) if mem is not None else None,  # type: ignore[arg-type]
                requests_per_day=profile.get("requests"),  # type: ignore[arg-type]
                network_mb_per_day=profile.get("network"),  # type: ignore[arg-type]
                observation_days=observation_days,
                attached=profile.get("attached"),  # type: ignore[arg-type]
                tags=dict(profile.get("tags", {})),  # type: ignore[call-overload]
                source="mock-synthetic",
            )
        )
    return samples
