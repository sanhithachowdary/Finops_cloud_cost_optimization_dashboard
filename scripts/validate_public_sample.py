"""Validate the engine on the FinOps Foundation public FOCUS 1.0 sample (anonymised real data).

    python -m scripts.validate_public_sample data/samples/focus_sample_10000.csv \
        --out docs/evidence/public-sample/report.json

Source: https://github.com/FinOps-Open-Cost-and-Usage-Spec/FOCUS-Sample-Data
(FOCUS-1.0/focus_sample_10000.csv, licence CC BY 4.0). The data file itself is NOT
committed. This is representative-shape validation, NOT a Q2 baseline for this project.

Checks: ingestion outcome and reconciliation; spend by provider vs an independent plain-CSV
oracle over the same accepted rows; anomaly, forecast and recommendation behaviour on a
short (about 1 month), hourly, multi-provider bill.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from collections import defaultdict
from pathlib import Path

os.environ.setdefault("FINOP_EVIDENCE_DIR", "")


def oracle_by_provider(path: Path, accepted_ids: set[str] | None) -> dict[str, float]:
    """Plain csv/float sum per provider (independent of pandas and the engine)."""
    totals: dict[str, float] = defaultdict(float)
    with path.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            if accepted_ids is not None and row.get("Id") not in accepted_ids:
                continue
            provider = {"Microsoft": "Azure"}.get(row["ProviderName"], row["ProviderName"])
            totals[provider] += float(row["BilledCost"])
    return {k: round(v, 6) for k, v in sorted(totals.items())}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_path")
    parser.add_argument("--out", default="docs/evidence/public-sample/report.json")
    args = parser.parse_args()

    from finops_engine.ai import AnomalyDetector
    from finops_engine.analytics import spend_breakdown, spend_overview, spend_trend
    from finops_engine.engine_config import EngineConfig
    from finops_engine.evaluation import backtest_forecast
    from finops_engine.ingestion import IngestionStore, ingest_csv_file
    from finops_engine.ingestion.validator import _clean, validate_rows
    from finops_engine.optimization.engine import generate_recommendations

    path = Path(args.csv_path)
    raw = path.read_bytes()
    cfg = EngineConfig()
    store = IngestionStore()
    report, rows = ingest_csv_file(path, store, cfg)
    records = store.snapshot_records()

    # Ids of rows the engine accepted = all ids minus quarantined/duplicate row numbers
    # (re-validated with the same config so the row numbers line up).
    result = validate_rows(rows, cfg.ingestion)
    rejected_rows = {q.row_number for q in result.quarantined} | set(result.duplicate_rows)
    accepted_ids = {_clean(r.get("Id")) for i, r in enumerate(rows, start=1) if i not in rejected_rows}

    engine_by_provider = {i["key"]: i["cost_usd"] for i in spend_breakdown(records, "provider")["items"]}
    oracle = oracle_by_provider(path, {str(x) for x in accepted_ids})
    mismatches = {
        k: {"engine": engine_by_provider.get(k), "oracle": round(v, 2)}
        for k, v in oracle.items()
        if engine_by_provider.get(k) is None or abs(engine_by_provider[k] - v) > 0.01
    }

    detector = AnomalyDetector.from_config(cfg.anomaly, cfg.version)
    anomalies = detector.detect_anomalies(records)
    recs = generate_recommendations(records, [], cfg, conflicting_resource_ids=store.conflicting_resource_ids)

    out = {
        "dataset": {
            "source": "https://github.com/FinOps-Open-Cost-and-Usage-Spec/FOCUS-Sample-Data",
            "file": "FOCUS-1.0/focus_sample_10000.csv",
            "licence": "CC BY 4.0 (FinOps Foundation)",
            "sha256": hashlib.sha256(raw).hexdigest(),
            "label": "anonymised real-world sample; NOT this project's Q2 baseline",
        },
        "config_version": cfg.version,
        "ingestion": {
            k: report[k]
            for k in (
                "status",
                "rows_received",
                "rows_accepted",
                "rows_quarantined",
                "rows_duplicate",
                "quarantine_reasons",
                "warnings",
                "transformations",
                "quarantine_ratio",
                "safe_mode_triggered",
                "reconciliation",
            )
        },
        "attribution": {
            "attributed_cost_share": report["attribution"]["attributed_cost_share"],
            "unattributed_rows": report["attribution"]["unattributed_rows"],
            "conflicting_resource_ids": len(report["attribution"]["conflicting_resource_ids"]),
        },
        "spend": {
            "overview": spend_overview(records),
            "by_provider_engine": engine_by_provider,
            "by_provider_oracle": {k: round(v, 2) for k, v in oracle.items()},
            "oracle_mismatches": mismatches,
            "oracle_status": "pass" if not mismatches else "fail",
            "daily_points": len(spend_trend(records, "daily")),
            "top_services": spend_breakdown(records, "service", top_n=5)["items"],
            "unattributed_resource_share": spend_breakdown(records, "resource")["unattributed_share"],
        },
        "anomalies": {"count": len(anomalies), "alerts": anomalies[:10]},
        "forecast_backtest": backtest_forecast(records, 7, min_history_days=14),
        "recommendations": {
            "summary_counts": recs["summary"]["counts"],
            "estimated_monthly_savings_usd": recs["summary"]["estimated_monthly_savings_usd"],
            "note": "No utilisation data in billing exports: right-sizing/idle not evaluated.",
        },
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({"ingestion": out["ingestion"]["status"], "oracle": out["spend"]["oracle_status"]}))


if __name__ == "__main__":
    main()
