"""Export recommendations and the savings summary to JSON or CSV.

    python -m scripts.export_recommendations --format csv --out output/recommendations.csv
    python -m scripts.export_recommendations --billing export.csv --utilization util.json --format json

(Previously an Enterprise-only banner; implemented for FR-4/5/6 evidence.)
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from finops_engine.engine_config import get_engine_config
from finops_engine.optimization.engine import generate_recommendations
from scripts.rightsizing import load_inputs

CSV_FIELDS = [
    "id",
    "kind",
    "status",
    "provider",
    "service",
    "resource_id",
    "action",
    "current_monthly_cost_usd",
    "estimated_monthly_savings_usd",
    "confidence",
    "reason",
    "safe_next_action",
    "config_version",
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Export FinOps recommendations")
    parser.add_argument("--billing")
    parser.add_argument("--utilization")
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--format", choices=["json", "csv"], default="json")
    parser.add_argument("--out", default="output/recommendations")
    args = parser.parse_args()

    records, samples, conflicts = load_inputs(args.billing, args.utilization, args.days)
    result = generate_recommendations(records, samples, get_engine_config(), conflicting_resource_ids=conflicts)
    out = Path(args.out)
    if out.suffix.lower() not in (".json", ".csv"):
        out = out.with_suffix(f".{args.format}")
    out.parent.mkdir(parents=True, exist_ok=True)
    if args.format == "json":
        out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    else:
        with out.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(result["recommendations"])
    print(f"Wrote {len(result['recommendations'])} recommendations to {out}")


if __name__ == "__main__":
    main()
