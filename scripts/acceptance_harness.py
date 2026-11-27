"""Acceptance / KPI harness (D5): KPI-1..KPI-6, spend oracle, fault campaign.

    python -m scripts.acceptance_harness [--baseline-bench path.json] [--out-dir docs/evidence]

Every number written by this harness is *measured* in the run that writes it,
on the versioned SYNTHETIC fixtures in ``tests/fixtures``.  Targets that are
defined relative to a "Q2 reference" are reported as ``blocked`` because no
Q2 baseline was supplied; see docs/evidence/q2-baseline-template.json.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures"
os.environ.setdefault("FINOP_EVIDENCE_DIR", "")  # harness keeps its evidence under --out-dir only


def _ingest(store, name: str) -> None:
    from finops_engine.ingestion import ingest_csv_file

    ingest_csv_file(FIX / name, store)


def spend_oracle() -> dict:
    from finops_engine.analytics import spend_breakdown, spend_overview, spend_trend
    from finops_engine.ingestion import IngestionStore

    store = IngestionStore()
    _ingest(store, "billing_small_v1.csv")
    recs = store.snapshot_records()
    exp = json.loads((FIX / "billing_small_v1.expected.json").read_text())
    checks = {
        "total": (spend_overview(recs)["total_cost_usd"], exp["total_billed_usd"]),
        **{
            f"provider:{k}": ({i["key"]: i["cost_usd"] for i in spend_breakdown(recs, "provider")["items"]}.get(k), v)
            for k, v in exp["by_provider"].items()
        },
        **{
            f"{g}:{k}": ({s["period"]: s["cost_usd"] for s in spend_trend(recs, g)}.get(k), v)
            for g in ("daily", "weekly", "monthly")
            for k, v in exp[g].items()
        },
    }
    mismatches = {k: v for k, v in checks.items() if v[0] is None or abs(v[0] - v[1]) > 0.005}
    return {"checks": len(checks), "mismatches": mismatches, "status": "pass" if not mismatches else "fail"}


def kpi_anomaly_and_forecast() -> dict:
    from finops_engine.ai import AnomalyDetector
    from finops_engine.engine_config import EngineConfig
    from finops_engine.evaluation import backtest_forecast, evaluate_anomaly_detector
    from finops_engine.ingestion import IngestionStore

    cfg = EngineConfig()
    out = {}
    for label, billing, labels in (
        ("development", "billing_benchmark_v1.csv", "anomaly_labels_v1.json"),
        ("holdout", "billing_holdout_v1.csv", "anomaly_labels_holdout_v1.json"),
    ):
        store = IngestionStore()
        _ingest(store, billing)
        recs = store.snapshot_records()
        gt = {(x["date"], x["provider"], x["service"]) for x in json.loads((FIX / labels).read_text())["labels"]}
        shipped = evaluate_anomaly_detector(recs, gt, AnomalyDetector.from_config(cfg.anomaly, cfg.version))
        legacy = evaluate_anomaly_detector(recs, gt, AnomalyDetector(min_relative_excess=0.0))
        out[label] = {
            "fixture": billing,
            "labels": labels,
            "anomaly_shipped_config": shipped,
            "anomaly_pre_change_rule (no materiality gate)": {
                k: legacy[k] for k in ("precision", "recall", "f1", "alerts", "false_positives")
            },
            "forecast_backtest": backtest_forecast(recs, cfg.forecast.backtest_holdout_days),
        }
    return out


def kpi_savings() -> dict:
    from finops_engine.engine_config import EngineConfig
    from finops_engine.ingestion import IngestionStore
    from finops_engine.optimization import UtilizationSample
    from finops_engine.optimization.engine import generate_recommendations

    store = IngestionStore()
    _ingest(store, "billing_benchmark_v1.csv")
    payload = json.loads((FIX / "utilization_benchmark_v1.json").read_text())
    samples = [UtilizationSample(**{k: v for k, v in s.items() if k != "expected"}) for s in payload["samples"]]
    result = generate_recommendations(store.snapshot_records(), samples, EngineConfig())
    s = result["summary"]
    return {
        "reference": s["reference"],
        "estimated_monthly_savings_usd": s["estimated_monthly_savings_usd"],
        "estimated_relative_savings_pct": s["estimated_relative_savings_pct"],
        "potential_requires_review_usd": s["potential_monthly_savings_requires_review_usd"],
        "by_kind": s["by_kind"],
        "counts": s["counts"],
        "realized_savings": s["realized_savings"],
    }


def _make_dependency(fault: str, state: dict):
    """A local fake dependency whose behaviour is controlled by ``state``."""

    def dep():
        if state["healthy"]:
            return "ok"
        if fault == "timeout":
            time.sleep(0.3)
            return "late"
        if fault == "flapping":
            state["flaps"] += 1
            if state["flaps"] % 2:
                raise RuntimeError("flap")
            return "ok"
        raise RuntimeError("injected")

    return dep


def fault_campaign(n_per_type: int = 5) -> dict:
    """KPI-4: inject faults into a local dependency and measure recovery."""
    from finops_engine.health import HealthRegistry
    from finops_engine.resilience import CircuitBreaker, call_with_resilience

    reset_s = 0.2
    max_recovery_s = 5.0
    trials = []
    for fault in ("timeout", "exception", "breaker_open", "flapping"):
        for i in range(n_per_type):
            registry = HealthRegistry()
            breaker = CircuitBreaker(failure_threshold=2, reset_s=reset_s)
            state = {"healthy": False, "flaps": 0}
            name = f"fault:{fault}:{i}"
            dep = _make_dependency(fault, state)

            injected_at = time.monotonic()
            for _ in range(3):  # drive the fault (and open the breaker); failures are expected
                with contextlib.suppress(Exception):
                    call_with_resilience(name, dep, timeout_s=0.1, retries=0, breaker=breaker, health=registry)
            detected = registry.component_status(name) in ("failed", "degraded")
            if fault == "breaker_open":
                detected = detected and breaker.state == "open"
            state["healthy"] = True  # heal the dependency
            recovered_at = None
            deadline = time.monotonic() + max_recovery_s
            while time.monotonic() < deadline:
                try:
                    call_with_resilience(name, dep, timeout_s=0.5, retries=0, breaker=breaker, health=registry)
                    recovered_at = time.monotonic()
                    break
                except Exception:  # noqa: BLE001
                    time.sleep(0.05)
            event = [e for e in registry.snapshot()["recent_events"] if e["event"] == "recovered"]
            trials.append(
                {
                    "fault": fault,
                    "trial": i,
                    "detected": detected,
                    "recovered": recovered_at is not None and registry.component_status(name) == "ok",
                    "recovery_event_recorded": bool(event) or fault == "flapping",
                    "seconds_to_recover_after_heal": round(recovered_at - injected_at, 3) if recovered_at else None,
                }
            )
    recovered = sum(t["recovered"] for t in trials)
    detected = sum(t["detected"] for t in trials)
    times = sorted(t["seconds_to_recover_after_heal"] for t in trials if t["seconds_to_recover_after_heal"])
    return {
        "faults_injected": len(trials),
        "detected": detected,
        "recovered": recovered,
        "recovery_rate": round(recovered / len(trials), 4),
        "detection_rate": round(detected / len(trials), 4),
        "max_seconds_fault_to_recovery": times[-1] if times else None,
        "breaker_reset_seconds": reset_s,
        "trials": trials,
    }


def run_benchmark(code_dir: Path, out: Path, trace: Path | None, reps: int) -> dict:
    cmd = [sys.executable, str(ROOT / "scripts" / "benchmark_api.py"), "--out", str(out), "--reps", str(reps)]
    if trace:
        cmd += ["--trace", str(trace)]
    env = dict(os.environ, FINOP_MOCK_MODE="true", FINOP_EVIDENCE_DIR="", PYTHONDONTWRITEBYTECODE="1")
    subprocess.run(cmd, cwd=code_dir, env=env, check=True, capture_output=True, text=True)
    return json.loads(out.read_text())


def ratio(a, b):
    return round(a / b, 4) if a is not None and b else None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", default=str(ROOT / "docs" / "evidence"))
    parser.add_argument("--baseline-code-dir", help="checkout of the pre-change code for a same-machine reference")
    parser.add_argument("--reps", type=int, default=30)
    args = parser.parse_args()
    # The KPI fixtures are USD-denominated; measure with the USD config (the shipped one reports in INR).
    os.environ.setdefault("FINOP_ENGINE_CONFIG", str(ROOT / "config" / "finops-engine.usd.json"))
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(ROOT))
    import logging

    logging.basicConfig(level=logging.ERROR)  # injected faults would otherwise flood the console

    from finops_engine import __version__
    from finops_engine.engine_config import EngineConfig

    started = time.time()
    results: dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "harness": "scripts/acceptance_harness.py",
        "app_version": __version__,
        "config_version": EngineConfig().version,
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "cpu_count": os.cpu_count(),
        },
        "data_label": "SYNTHETIC versioned fixtures (tests/fixtures, generator benchmark-v1)",
    }
    results["spend_oracle"] = spend_oracle()
    results["anomaly_forecast"] = kpi_anomaly_and_forecast()
    results["savings"] = kpi_savings()
    results["fault_campaign"] = fault_campaign()
    final = run_benchmark(ROOT, out_dir / "bench-final.json", out_dir / "workload-trace-final.csv", args.reps)
    results["benchmark_final"] = final
    if args.baseline_code_dir:
        base = run_benchmark(
            Path(args.baseline_code_dir),
            out_dir / "bench-baseline.json",
            out_dir / "workload-trace-baseline.csv",
            args.reps,
        )
        results["benchmark_pre_change_reference"] = base
    else:
        base = None

    dev = results["anomaly_forecast"]["development"]
    hold = results["anomaly_forecast"]["holdout"]
    sav = results["savings"]
    kpis = [
        {
            "kpi": "KPI-1 estimated savings",
            "measured": f"{sav['estimated_relative_savings_pct']}% of reference monthly cost (benchmark fixture)",
            "baseline": "Q2 reference NOT supplied",
            "formula": "sum(actionable est. monthly savings) / trailing-30-day cost x 30",
            "target": ">= 5% relative improvement vs Q2 reference",
            "status": "simulated-only; vs-Q2 BLOCKED (no Q2 baseline)",
        },
        {
            "kpi": "KPI-2 forecast accuracy",
            "measured": f"dev MAPE {dev['forecast_backtest']['model']['mape_pct']}% / MAE "
            f"${dev['forecast_backtest']['model']['mae_usd']}; holdout MAPE "
            f"{hold['forecast_backtest']['model']['mape_pct']}% / MAE ${hold['forecast_backtest']['model']['mae_usd']}",
            "baseline": f"naive 7-day mean MAE dev ${dev['forecast_backtest']['baselines']['naive_mean_7d']['mae_usd']}"
            f", holdout ${hold['forecast_backtest']['baselines']['naive_mean_7d']['mae_usd']}",
            "formula": "MAE, RMSE, MAPE on last 14 held-out days",
            "target": "measure and report against approved reference data",
            "status": "measured (synthetic); approved reference data not supplied",
        },
        {
            "kpi": "KPI-3 anomaly precision",
            "measured": f"dev P={dev['anomaly_shipped_config']['precision']} "
            f"R={dev['anomaly_shipped_config']['recall']}; "
            f"holdout P={hold['anomaly_shipped_config']['precision']} "
            f"R={hold['anomaly_shipped_config']['recall']}",
            "baseline": f"pre-change rule: dev P="
            f"{dev['anomaly_pre_change_rule (no materiality gate)']['precision']}, holdout P="
            f"{hold['anomaly_pre_change_rule (no materiality gate)']['precision']}",
            "formula": "TP / (TP + FP) vs injected-label oracle",
            "target": "calculate from versioned fixtures and independent ground truth",
            "status": "measured (synthetic)",
        },
        {
            "kpi": "KPI-4 recovery rate",
            "measured": f"{results['fault_campaign']['recovered']}/{results['fault_campaign']['faults_injected']} "
            f"= {results['fault_campaign']['recovery_rate']}",
            "baseline": "pre-change code had no breaker/recovery tracking",
            "formula": "recovered faults / injected faults (local fault injection)",
            "target": "measure successful recoveries after fault injection",
            "status": "measured (local, simulated faults)",
        },
        {
            "kpi": "KPI-5 service error rate",
            "measured": f"{final['server_error_rate']} ({final['server_errors']}/{final['requests']})",
            "baseline": f"pre-change same workload: {base['server_error_rate']}" if base else "not run",
            "formula": "5xx responses / requests (workload api-workload-v1)",
            "target": "<= 0.8 x Q2 reference",
            "status": "measured; ratio vs Q2 BLOCKED (no Q2 baseline); both runs 0 errors -> ratio undefined"
            if base and not base["server_error_rate"] and not final["server_error_rate"]
            else "measured; vs Q2 BLOCKED",
        },
        {
            "kpi": "KPI-6 p95 latency",
            "measured": f"{final['p95_ms']} ms (in-process, shared endpoints)",
            "baseline": f"pre-change same machine/workload: {base['p95_ms']} ms (ratio "
            f"{ratio(final['p95_ms'], base['p95_ms'])})"
            if base
            else "not run",
            "formula": "nearest-rank p95 of sequential request latency",
            "target": "<= 0.80 x Q2 reference",
            "status": "measured vs pre-change proxy; vs Q2 BLOCKED",
        },
    ]
    results["kpi_table"] = kpis
    results["harness_seconds"] = round(time.time() - started, 1)
    (out_dir / "kpi-results.json").write_text(json.dumps(results, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(kpis, indent=2))


if __name__ == "__main__":
    main()
