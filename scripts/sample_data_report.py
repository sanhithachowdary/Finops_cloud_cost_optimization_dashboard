"""Run the full engine on the sample datasets and write an output report (HTML, Markdown, JSON).

    python -m scripts.sample_data_report                    # all CSVs in data/demo/
    python -m scripts.sample_data_report data/demo/x.csv    # specific files

Uses config/finops-engine.sample.json (version 2026.10-v2-sample-inr): amounts are reported in
Indian rupees, converted from the USD bills at the rate set in the config (ingestion.fx_rates_to_base);
anomaly floor about Rs 95/day (= $1) for these small bills. Output: output/sample-report/report.{html,md,json}.

The sample datasets are GENERATED data. The instructor approved them as the reference data
for this college project; the report says so on every page. The Q2 reference file is
data/demo/focus_compliant_demo_q2.csv (1 Apr - 30 Jun 2026).
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from finops_engine.currency import format_money

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_CONFIG = ROOT / "config" / "finops-engine.sample.json"

Q2_FILE = "focus_compliant_demo_q2.csv"
DATA_LABEL = (
    "GENERATED sample data, approved by the course instructor as the reference data for this "
    "project. Not a real cloud bill."
)


def _event(event: str, provider: str, service: str, start: str, end: str, kind: str) -> dict[str, str]:
    return {"event": event, "provider": provider, "service": service, "start": start, "end": end, "kind": kind}


# Problems planted by the generator scripts: the ground truth for anomaly precision/recall (KPI-3).
PLANTED: dict[str, list[dict[str, str]]] = {
    "focus_bill_jan_mar.csv": [
        _event("AWS serverless 15-25x usage spike", "AWS", "AWS_Serverless", "2026-02-20", "2026-02-25", "spike")
    ],
    "focus_bill_mar_may.csv": [
        _event("Storage grows every day (no clean-up)", "*", "*_Storage", "2026-03-01", "2026-05-31", "drift")
    ],
    "focus_bill_may_june.csv": [
        _event("GCP compute doubles overnight", "GCP", "GCP_Compute", "2026-06-10", "2026-06-30", "step")
    ],
    Q2_FILE: [],
}

# Fields copied from the engine's results into the report.
INGESTION_FIELDS = [
    "status",
    "rows_received",
    "rows_accepted",
    "rows_quarantined",
    "rows_duplicate",
    "quarantine_reasons",
    "warnings",
    "safe_mode_triggered",
    "reconciliation",
]
ALERT_FIELDS = [
    "date",
    "provider",
    "service",
    "actual_cost_usd",
    "expected_baseline_usd",
    "anomaly_excess_usd",
    "severity",
    "reason",
]
FORECAST_FIELDS = [
    "status",
    "total_projected_spend_usd",
    "average_daily_projected_usd",
    "trend_usd_per_day",
    "history_days",
]
RECOMMENDATION_FIELDS = [
    "kind",
    "status",
    "provider",
    "service",
    "resource_id",
    "action",
    "estimated_monthly_savings_usd",
    "confidence",
    "reason",
]
SAVINGS_FIELDS = [
    "estimated_monthly_savings_usd",
    "estimated_relative_savings_pct",
    "potential_monthly_savings_requires_review_usd",
    "reference",
    "counts",
]


def pick(d: dict[str, Any], fields: list[str]) -> dict[str, Any]:
    return {k: d.get(k) for k in fields}


def _matches(alert: dict[str, Any], event: dict[str, Any]) -> bool:
    if not (event["start"] <= alert["date"] <= event["end"]):
        return False
    if event["provider"] != "*" and alert["provider"] != event["provider"]:
        return False
    svc = event["service"]
    return alert["service"].endswith(svc[1:]) if svc.startswith("*") else alert["service"] == svc


def analyse_file(path: Path) -> dict[str, Any]:
    from finops_engine.ai import AnomalyDetector, CostForecaster
    from finops_engine.analytics import spend_breakdown, spend_overview, spend_trend
    from finops_engine.engine_config import get_engine_config
    from finops_engine.evaluation import backtest_forecast
    from finops_engine.ingestion import IngestionStore, ingest_csv_file
    from finops_engine.ingestion.utilization_column import DEFAULT_COLUMN, samples_from_billing_rows
    from finops_engine.optimization.engine import generate_recommendations

    cfg = get_engine_config()
    store = IngestionStore()
    report, rows = ingest_csv_file(path, store, cfg)
    records = store.snapshot_records()
    samples, skipped = samples_from_billing_rows(rows) if rows and DEFAULT_COLUMN in rows[0] else ([], [])

    alerts = AnomalyDetector.from_config(cfg.anomaly, cfg.version).detect_anomalies(records)
    forecast = CostForecaster(forecast_days=30, min_history_days=14).predict_future_cost(records)
    backtest = backtest_forecast(records, holdout_days=7, min_history_days=14)
    recs = generate_recommendations(records, samples, cfg, conflicting_resource_ids=store.conflicting_resource_ids)

    planted = PLANTED.get(path.name)
    evaluation = None
    if planted is not None:
        point_events = [e for e in planted if e["kind"] in ("spike", "step")]  # drift is gradual, not a point anomaly
        correct = [a for a in alerts if any(_matches(a, e) for e in point_events)]
        evaluation = {
            "planted_events": planted,
            "alerts": len(alerts),
            "true_positive_alerts": len(correct),
            "false_positive_alerts": len(alerts) - len(correct),
            "point_events": len(point_events),
            "point_events_detected": [e["event"] for e in point_events if any(_matches(a, e) for a in alerts)],
            "note": "Drift events are gradual by design and are not counted as point anomalies.",
        }

    return {
        "file": path.name,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "ingestion": pick(report, INGESTION_FIELDS),
        "overview": spend_overview(records),
        "by_provider": spend_breakdown(records, "provider")["items"],
        "by_service": spend_breakdown(records, "service", top_n=12)["items"],
        "monthly": spend_trend(records, "monthly"),
        "daily": [{"period": d["period"], "cost_usd": d["cost_usd"]} for d in spend_trend(records, "daily")],
        "anomalies": [pick(a, ALERT_FIELDS) for a in alerts],
        "anomaly_evaluation": evaluation,
        "forecast_next_30_days": pick(forecast, FORECAST_FIELDS),
        "backtest": backtest,
        "utilisation_samples": len(samples),
        "utilisation_skipped": len(skipped),
        "recommendations": [pick(r, RECOMMENDATION_FIELDS) for r in recs["recommendations"]],
        "savings": pick(recs["summary"], SAVINGS_FIELDS),
        "config_version": cfg.version,
    }


def kpi(name: str, measured: str, target: str, result: str, basis: str) -> dict[str, str]:
    return {"kpi": name, "measured": measured, "target": target, "result": result, "basis": basis}


def kpi_table(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    q2 = next((f for f in files if f["file"] == Q2_FILE), None)
    if q2:
        sv = q2["savings"]
        pct = sv["estimated_relative_savings_pct"]
        measured = (
            f"{pct}% ({_money(sv['estimated_monthly_savings_usd'])} of "
            f"{_money(sv['reference']['monthly_cost_usd'])}/month)"
        )
        rows.append(
            kpi(
                "KPI-1 Estimated savings vs Q2 reference",
                measured,
                ">= 5%",
                "MET" if pct is not None and pct >= 5 else "NOT MET",
                f"{Q2_FILE} (Apr-Jun 2026); actionable recommendations only",
            )
        )

    mapes = [
        f"{f['file']}: {f['backtest']['model']['mape_pct']}%"
        + ("" if f["backtest"].get("beats_best_naive_baseline_on_mae") else " (naive was better)")
        for f in files
        if f["backtest"].get("status") == "ok"
    ]
    rows.append(
        kpi(
            "KPI-2 Forecast accuracy",
            "; ".join(mapes),
            "Measure and report against approved reference data",
            "REPORTED",
            "7-day hold-out back-test (MAPE) per file",
        )
    )

    evals = [f["anomaly_evaluation"] for f in files if f["anomaly_evaluation"]]
    tp, alerts = sum(e["true_positive_alerts"] for e in evals), sum(e["alerts"] for e in evals)
    found, events = sum(len(e["point_events_detected"]) for e in evals), sum(e["point_events"] for e in evals)
    precision = round(tp / alerts, 3) if alerts else None
    rows.append(
        kpi(
            "KPI-3 Anomaly precision",
            f"precision {precision} ({tp}/{alerts} alerts correct); recall {found}/{events} planted spike/step events",
            "Compute against independent ground truth",
            "REPORTED",
            "Planted scenarios from the generator scripts (see PLANTED in the script)",
        )
    )

    # KPI-4..6 are service measurements, read from the KPI harness results.
    ref_path = ROOT / "docs" / "evidence" / "kpi-results.json"
    if ref_path.exists():
        ref = json.loads(ref_path.read_text(encoding="utf-8"))
        final, base = ref.get("benchmark_final"), ref.get("benchmark_pre_change_reference")
        fault = ref.get("fault_campaign")
        same_host = "Reference = original v1.3.0 code, same machine and workload"
        if fault:
            rows.append(
                kpi(
                    "KPI-4 Recovery rate",
                    f"{fault.get('recovered')}/{fault.get('faults_injected')} faults recovered",
                    "Measure recoveries after fault injection",
                    "REPORTED",
                    "docs/evidence/kpi-results.json fault campaign",
                )
            )
        if final and base:
            fe, be = final["server_error_rate"], base["server_error_rate"]
            rows.append(
                kpi(
                    "KPI-5 Service error rate",
                    f"{fe} ({final['server_errors']}/{final['requests']}) vs reference {be}",
                    "<= 0.8 x reference",
                    "MET" if fe <= 0.8 * be or fe == 0 else "NOT MET",
                    same_host,
                )
            )
            ratio = round(final["p95_ms"] / base["p95_ms"], 3)
            rows.append(
                kpi(
                    "KPI-6 p95 latency",
                    f"{final['p95_ms']} ms vs reference {base['p95_ms']} ms (ratio {ratio})",
                    "<= 0.80 x reference",
                    "MET" if ratio <= 0.8 else "NOT MET",
                    same_host,
                )
            )
    return rows


# rendering


def _currency_note() -> tuple[str, str]:
    """(currency code, human note on the conversion) from the active engine config."""
    from finops_engine.engine_config import get_engine_config

    ing = get_engine_config().ingestion
    rates = ", ".join(
        f"1 {cur} = {format_money(rate, ing.base_currency)}" for cur, rate in sorted(ing.fx_rates_to_base.items())
    )
    note = f"All amounts in {ing.base_currency}" + (f", converted at {rates} (rate set in the config)" if rates else "")
    return ing.base_currency, note


def _money(v: float | None) -> str:
    return format_money(v, _currency_note()[0])


def _sparkline(daily: list[dict[str, Any]], width: int = 640, height: int = 120) -> str:
    vals = [d["cost_usd"] for d in daily]
    if len(vals) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1.0
    pts = " ".join(
        f"{i * (width - 8) / (len(vals) - 1) + 4:.1f},{height - 14 - (v - lo) / span * (height - 28):.1f}"
        for i, v in enumerate(vals)
    )
    return (
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="Daily cost">'
        f'<polyline fill="none" stroke="var(--accent)" stroke-width="2" points="{pts}"/>'
        f'<text x="4" y="12" class="ax">{_money(hi)}</text>'
        f'<text x="4" y="{height - 2}" class="ax">{_money(lo)} · {daily[0]["period"]} → {daily[-1]["period"]}</text>'
        "</svg>"
    )


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    head = "".join(f"<th>{html.escape(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{html.escape(str(c))}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def render_html(report: dict[str, Any]) -> str:
    parts = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>FinOps Sample Report</title><style>"
        ":root{--bg:#fbfbfa;--fg:#1d1d1f;--muted:#6b6b70;--line:#e3e3e0;--accent:#2f6fde;--ok:#1f7a4d;--bad:#b3261e}"
        "@media (prefers-color-scheme:dark){:root{--bg:#161618;--fg:#ececec;--muted:#a0a0a6;--line:#2c2c30;"
        "--accent:#7aa7ff;--ok:#5fcf95;--bad:#ff8a80}}"
        "body{background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,Segoe UI,sans-serif;margin:0 auto;"
        "max-width:1080px;padding:24px 16px}h1{font-size:26px;margin:0 0 4px}h2{margin-top:40px;border-top:1px "
        "solid var(--line);padding-top:24px}h3{margin:24px 0 8px;font-size:16px}.muted{color:var(--muted)}"
        ".label{border:1px solid var(--line);border-radius:8px;padding:10px 14px;margin:12px 0}"
        "table{border-collapse:collapse;width:100%;margin:8px 0;font-size:14px;display:block;overflow-x:auto}"
        "th,td{border-bottom:1px solid var(--line);padding:6px 10px;text-align:left;vertical-align:top}"
        "th{color:var(--muted);font-weight:600}.MET{color:var(--ok);font-weight:700}"
        ".NOT{color:var(--bad);font-weight:700}svg{width:100%;max-width:640px;height:auto}"
        ".ax{font-size:11px;fill:var(--muted)}.grid{display:grid;grid-template-columns:repeat(auto-fit,"
        "minmax(160px,1fr));gap:12px}.stat{border:1px solid var(--line);border-radius:8px;padding:10px 12px}"
        ".stat b{display:block;font-size:20px}</style></head><body>",
        "<h1>FinOps engine output on the sample data</h1>",
        f"<p class='muted'>Generated {html.escape(report['generated_at'])} · config "
        f"{html.escape(report['config_version'])} · app {html.escape(report['app_version'])}</p>",
        f"<div class='label'><b>Data:</b> {html.escape(DATA_LABEL)}<br><b>Currency:</b> "
        f"{html.escape(report.get('currency_note', ''))}</div>",
        "<h2>KPI summary</h2>",
    ]
    kpi_rows = "".join(
        f"<tr><td>{html.escape(k['kpi'])}</td><td>{html.escape(k['measured'])}</td><td>{html.escape(k['target'])}"
        f"</td><td class='{k['result'].split()[0]}'>{html.escape(k['result'])}</td><td>{html.escape(k['basis'])}"
        "</td></tr>"
        for k in report["kpis"]
    )
    parts.append(
        "<table><thead><tr><th>KPI</th><th>Measured</th><th>Target</th><th>Result</th><th>Basis</th></tr>"
        f"</thead><tbody>{kpi_rows}</tbody></table>"
    )
    for f in report["files"]:
        ov, ing, sv, fc = f["overview"], f["ingestion"], f["savings"], f["forecast_next_30_days"]
        by_prov, by_svc = f["by_provider"], f["by_service"]
        parts.append(f"<h2>{html.escape(f['file'])}</h2>")
        parts.append(
            "<div class='grid'>"
            f"<div class='stat'>Total spend<b>{_money(ov['total_cost_usd'])}</b></div>"
            f"<div class='stat'>Period<b>{ov['period_start']} → {ov['period_end']}</b></div>"
            f"<div class='stat'>Rows accepted / rejected<b>{ing['rows_accepted']} / {ing['rows_quarantined']}</b></div>"
            f"<div class='stat'>Anomalies<b>{len(f['anomalies'])}</b></div>"
            f"<div class='stat'>Next 30 days (forecast)<b>{_money(fc['total_projected_spend_usd'])}</b></div>"
            f"<div class='stat'>Estimated savings<b>{_money(sv['estimated_monthly_savings_usd'])}/mo</b>"
            f"<span class='muted'>{sv['estimated_relative_savings_pct']}% of reference</span></div></div>"
        )
        parts.append("<h3>Daily cost</h3>" + _sparkline(f["daily"]))
        parts.append(
            "<h3>Spend by provider</h3>"
            + _table(["Provider", "Cost", "Share"], [[i["key"], _money(i["cost_usd"]), i["share"]] for i in by_prov])
        )
        parts.append(
            "<h3>Spend by service</h3>"
            + _table(["Service", "Cost", "Share"], [[i["key"], _money(i["cost_usd"]), i["share"]] for i in by_svc])
        )
        parts.append(
            "<h3>Monthly trend</h3>"
            + _table(["Month", "Cost"], [[m["period"], _money(m["cost_usd"])] for m in f["monthly"]])
        )
        if f["anomalies"]:
            parts.append(
                "<h3>Anomalies</h3>"
                + _table(
                    ["Date", "Provider", "Service", "Actual", "Expected", "Severity", "Reason"],
                    [
                        [
                            a["date"],
                            a["provider"],
                            a["service"],
                            _money(a["actual_cost_usd"]),
                            _money(a["expected_baseline_usd"]),
                            a["severity"],
                            a["reason"],
                        ]
                        for a in f["anomalies"]
                    ],
                )
            )
        else:
            parts.append("<h3>Anomalies</h3><p>None detected.</p>")
        ev = f["anomaly_evaluation"]
        if ev and ev["planted_events"]:
            parts.append(
                "<p class='muted'>Planted in this file: "
                + "; ".join(
                    html.escape(f"{e['event']} ({e['start']} to {e['end']}, {e['kind']})") for e in ev["planted_events"]
                )
                + f". Detected: {html.escape(', '.join(ev['point_events_detected']) or 'none')}.</p>"
            )
        bt = f["backtest"]
        if bt.get("status") == "ok":
            parts.append(
                "<h3>Forecast check (last 7 days held out)</h3>"
                + _table(
                    ["Model", "MAE", "MAPE"],
                    [["Linear trend (this engine)", _money(bt["model"]["mae_usd"]), f"{bt['model']['mape_pct']}%"]]
                    + [[name, _money(m["mae_usd"]), f"{m['mape_pct']}%"] for name, m in bt["baselines"].items()],
                )
            )
        recs = [r for r in f["recommendations"] if r["status"] in ("actionable", "requires_review")]
        parts.append(
            "<h3>Recommendations</h3>"
            + (
                _table(
                    ["Kind", "Status", "Service", "Savings/mo", "Reason"],
                    [
                        [r["kind"], r["status"], r["service"], _money(r["estimated_monthly_savings_usd"]), r["reason"]]
                        for r in recs
                    ],
                )
                if recs
                else "<p>None.</p>"
            )
            + f"<p class='muted'>Utilisation samples derived: {f['utilisation_samples']} "
            f"(skipped {f['utilisation_skipped']} storage/serverless). Commitment savings use ASSUMED "
            "discount rates. Nothing is applied automatically.</p>"
        )
    parts.append("</body></html>")
    return "".join(parts)


def render_md(report: dict[str, Any]) -> str:
    out = [
        "# FinOps engine output on the sample data",
        "",
        f"Generated {report['generated_at']} · config `{report['config_version']}`",
        "",
        f"> {DATA_LABEL}",
        ">",
        f"> {report.get('currency_note', '')}",
        "",
        "## KPI summary",
        "",
        "| KPI | Measured | Target | Result | Basis |",
        "|---|---|---|---|---|",
    ]
    out += [
        f"| {k['kpi']} | {k['measured']} | {k['target']} | **{k['result']}** | {k['basis']} |" for k in report["kpis"]
    ]
    for f in report["files"]:
        ov, sv = f["overview"], f["savings"]
        out += [
            "",
            f"## {f['file']}",
            "",
            f"- Period {ov['period_start']} to {ov['period_end']}; total {_money(ov['total_cost_usd'])}; "
            f"{f['ingestion']['rows_accepted']} rows accepted, {f['ingestion']['rows_quarantined']} rejected",
            "- By provider: " + ", ".join(f"{i['key']} {_money(i['cost_usd'])}" for i in f["by_provider"]),
            f"- Anomalies: {len(f['anomalies'])}"
            + "".join(f"\n  - {a['date']} {a['provider']} {a['service']}: {a['reason']}" for a in f["anomalies"]),
            f"- Forecast next 30 days: {_money(f['forecast_next_30_days']['total_projected_spend_usd'])}",
            f"- Estimated savings: {_money(sv['estimated_monthly_savings_usd'])}/month "
            f"({sv['estimated_relative_savings_pct']}% of {_money(sv['reference']['monthly_cost_usd'])}/month)",
        ]
    return "\n".join(out) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files", nargs="*")
    parser.add_argument("--out-dir", default=str(ROOT / "output" / "sample-report"))
    args = parser.parse_args()
    # Set before the engine config is first loaded (it is cached per process).
    os.environ.setdefault("FINOP_EVIDENCE_DIR", "")
    os.environ.setdefault("FINOP_ENGINE_CONFIG", str(SAMPLE_CONFIG))

    from finops_engine import __version__
    from finops_engine.engine_config import get_engine_config

    paths = [Path(p) for p in args.files] or sorted((ROOT / "data" / "demo").glob("*.csv"))
    if not paths:
        raise SystemExit("no CSV files given and none found in data/demo/")
    files = []
    for p in paths:
        print(f"analysing {p.name} ...", flush=True)
        files.append(analyse_file(p))
    report: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "app_version": __version__,
        "config_version": get_engine_config().version,
        "data_label": DATA_LABEL,
        "currency": _currency_note()[0],
        "currency_note": _currency_note()[1],
        "kpis": kpi_table(files),
        "files": files,
    }
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    (out / "report.md").write_text(render_md(report), encoding="utf-8")
    (out / "report.html").write_text(render_html(report), encoding="utf-8")
    for k in report["kpis"]:
        print(f"{k['kpi']:<42} {k['result']:<9} {k['measured']}")
    print(f"\nwrote {out / 'report.html'}")


if __name__ == "__main__":
    main()
