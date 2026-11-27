"""Upload several generated bills to the API, one after another, and check the result.

    python -m scripts.multi_bill_check                     # Jan-Mar (USD) + Mar-Jun (INR, overlaps March) + Jul-Sep
    python -m scripts.multi_bill_check --out docs/evidence/multi-bill/report.json

Runs the real FastAPI app in-process (no server, no network) with the shipped INR config and
checks, against independent calculations:
  * every bill is accepted, overlapping days are counted once, re-uploading a bill is a no-op;
  * total and per-provider spend equal a plain pandas sum over distinct line Ids;
  * every planted spike/step (from the bills' .labels.json ground truth) is detected;
  * forecast, back-test, recommendations and savings run on the combined history.
"""

from __future__ import annotations

import argparse
import io
import json
import os
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
BILLS = (
    (date(2026, 1, 1), date(2026, 3, 31), "USD"),
    (date(2026, 3, 1), date(2026, 6, 30), "INR"),
    (date(2026, 7, 1), date(2026, 9, 30), "USD"),
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default=str(ROOT / "output" / "multi-bill" / "report.json"))
    args = parser.parse_args()
    os.environ.setdefault("FINOP_EVIDENCE_DIR", "")
    os.environ.setdefault("FINOP_MOCK_MODE", "true")
    os.environ.setdefault("FINOP_ENGINE_CONFIG", str(ROOT / "config" / "finops-engine.json"))

    from fastapi.testclient import TestClient

    from finops_engine.api.app import app
    from finops_engine.engine_config import get_engine_config
    from finops_engine.ingestion import parse_csv_text
    from finops_engine.ingestion.utilization_column import samples_from_billing_rows
    from scripts.period_bills import generate_period

    cfg = get_engine_config()
    rate = cfg.ingestion.fx_rates_to_base.get("USD", 1.0)
    client = TestClient(app)
    days = 365
    q = {"source": "ingested", "days": days}

    bills, uploads, labels = [], [], set()
    for start, end, cur in BILLS:
        df, meta = generate_period(start, end, currency=cur, fx=1.0 if cur == "USD" else rate)
        name = f"focus_bill_{start}_{end}{'' if cur == 'USD' else '_inr'}.csv"
        buf = io.StringIO()
        df.to_csv(buf, index=False, lineterminator="\n")
        body = buf.getvalue().encode()
        bills.append((name, df, body))
        labels |= {(x["date"], x["provider"], x["service"]) for x in meta["labels"]}
    for name, _, body in [*bills, bills[0]]:  # the last upload repeats the first bill
        r = client.post(
            "/api/v1/ingest/billing",
            params={"format": "csv", "source_name": name},
            content=body,
            headers={"Content-Type": "text/csv"},
        ).json()
        uploads.append(
            {
                k: r.get(k)
                for k in (
                    "source_name",
                    "rows_received",
                    "rows_accepted",
                    "rows_duplicate",
                    "rows_quarantined",
                    "quarantine_reasons",
                    "idempotent_replay",
                )
            }
        )
    for _, _, body in bills:
        samples, _ = samples_from_billing_rows(parse_csv_text(body.decode(), 10**7))
        client.post("/api/v1/ingest/utilization", json={"samples": [s.model_dump() for s in samples]})

    frames = []
    for _, df, _ in bills:
        d = df.copy()
        d["base"] = d["BilledCost"] * d["BillingCurrency"].map({"USD": rate, cfg.ingestion.base_currency: 1.0})
        frames.append(d)
    oracle = pd.concat(frames).drop_duplicates("Id")
    ov = client.get("/api/v1/costs/overview", params=q).json()
    by_provider = {
        i["key"]: i["cost_usd"]
        for i in client.get("/api/v1/costs/breakdown", params={**q, "dimension": "provider"}).json()["items"]
    }
    oracle_provider = oracle.groupby("ProviderName")["base"].sum().round(2).to_dict()
    alerts = client.get("/api/v1/anomalies/detect", params=q).json()["anomalies"]
    found = {(a["date"], a["provider"], a["service"]) for a in alerts}
    recs = client.get("/api/v1/recommendations", params=q).json()
    result: dict[str, Any] = {
        "data_label": "GENERATED bills (scripts/period_bills.py, seed 2026, events preset 'demo'); not real bills",
        "config_version": cfg.version,
        "currency": cfg.ingestion.base_currency,
        "uploads": uploads,
        "spend": {
            "engine_total": ov["overview"]["total_cost_usd"],
            "oracle_total": round(float(oracle["base"].sum()), 2),
            "distinct_lines": len(oracle),
            "period": [ov["overview"]["period_start"], ov["overview"]["period_end"]],
            "engine_by_provider": by_provider,
            "oracle_by_provider": oracle_provider,
        },
        "anomalies": {
            "alerts": [
                (a["date"], a["provider"], a["service"], a["actual_cost_usd"], a["expected_cost_usd"]) for a in alerts
            ],
            "planted": sorted(labels),
            "true_positives": len(found & labels),
            "false_positives": sorted(found - labels),
            "missed": sorted(labels - found),
            "precision": round(len(found & labels) / len(found), 3) if found else None,
            "recall": round(len(found & labels) / len(labels), 3) if labels else None,
        },
        "forecast": {
            k: v
            for k, v in client.get("/api/v1/forecast/predict", params={"source": "ingested", "history_days": days})
            .json()
            .items()
            if k != "forecast"
        },
        "backtest": client.get("/api/v1/forecast/backtest", params=q).json(),
        "recommendations": recs["summary"]["counts"],
        "savings": {
            k: recs["summary"][k]
            for k in (
                "estimated_monthly_savings_usd",
                "potential_monthly_savings_requires_review_usd",
                "estimated_relative_savings_pct",
            )
        },
    }
    checks = {
        "all_bills_accepted_without_quarantine": all(u["rows_quarantined"] == 0 for u in uploads),
        "overlap_counted_once": uploads[1]["rows_duplicate"] > 0,
        "reupload_is_idempotent": bool(uploads[-1]["idempotent_replay"]),
        "total_matches_oracle": abs(result["spend"]["engine_total"] - result["spend"]["oracle_total"]) <= 1.0,
        "providers_match_oracle": all(abs(by_provider.get(k, 0) - v) <= 1.0 for k, v in oracle_provider.items()),
        "all_planted_anomalies_found": not result["anomalies"]["missed"],
    }
    result["checks"] = checks
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8")
    for k, v in checks.items():
        print(f"{'PASS' if v else 'FAIL'}  {k}")
    a = result["anomalies"]
    print(f"anomalies: {a['true_positives']}/{len(a['planted'])} planted found, precision {a['precision']}")
    print(f"wrote {out}")
    if not all(checks.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
