"""Right-sizing, idle-resource and commitment recommendations from the command line.

    python -m scripts.rightsizing                                   # SYNTHETIC mock data
    python -m scripts.rightsizing --billing export.csv --utilization util.json

Recommendations are advisory only; this tool never changes cloud resources.
(Previously an Enterprise-only banner; implemented for FR-4/5/6.)
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from finops_engine.connectors import MockTelemetryConnector
from finops_engine.connectors.mock_connector import mock_utilization
from finops_engine.engine_config import get_engine_config
from finops_engine.ingestion import IngestionStore, ingest_csv_file
from finops_engine.optimization import UtilizationSample
from finops_engine.optimization.engine import generate_recommendations


def load_inputs(billing: str | None, utilization: str | None, days: int):
    if billing:
        store = IngestionStore()
        report, _ = ingest_csv_file(billing, store)
        print(
            f"Ingested {report['rows_accepted']} rows ({report['rows_quarantined']} quarantined, "
            f"{report['rows_duplicate']} duplicates) from {billing}"
        )
        records, conflicts = store.snapshot_records(), store.conflicting_resource_ids
    else:
        print(f"Using {days} days of SYNTHETIC mock billing data")
        records, conflicts = MockTelemetryConnector(days=days).fetch_cost_data(), set()
    if utilization:
        payload = json.loads(Path(utilization).read_text(encoding="utf-8"))
        items = payload["samples"] if isinstance(payload, dict) else payload
        samples = [UtilizationSample(**{k: v for k, v in s.items() if k != "expected"}) for s in items]
    elif billing:
        samples = []
        print("No --utilization given: right-sizing and idle detection are NOT evaluated")
    else:
        samples = mock_utilization()
    return records, samples, conflicts


def main() -> None:
    parser = argparse.ArgumentParser(description="FinOps optimisation recommendations (advisory only)")
    parser.add_argument("--billing", help="FOCUS-style billing CSV")
    parser.add_argument("--utilization", help="utilisation samples JSON")
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--kind", choices=["rightsizing", "idle", "commitment"])
    args = parser.parse_args()

    records, samples, conflicts = load_inputs(args.billing, args.utilization, args.days)
    result = generate_recommendations(records, samples, get_engine_config(), conflicting_resource_ids=conflicts)
    for rec in result["recommendations"]:
        if args.kind and rec["kind"] != args.kind:
            continue
        savings = rec["estimated_monthly_savings_usd"]
        print(
            f"[{rec['status']:>17}] {rec['kind']:<11} {rec['resource_id'] or rec['service']}: {rec['action']}"
            f" | est. ${savings:,.2f}/mo"
            if savings is not None
            else f"[{rec['status']:>17}] {rec['kind']:<11} {rec['resource_id'] or rec['service']}: {rec['action']}"
        )
    s = result["summary"]
    print(
        f"\nEstimated (actionable) savings: ${s['estimated_monthly_savings_usd']:,.2f}/month "
        f"= {s['estimated_relative_savings_pct']}% of reference ${s['reference']['monthly_cost_usd']:,.2f}/month"
    )
    print(f"Potential, requires review:     ${s['potential_monthly_savings_requires_review_usd']:,.2f}/month")
    print(f"Realised savings:               {s['realized_savings']['status']}")


if __name__ == "__main__":
    main()
