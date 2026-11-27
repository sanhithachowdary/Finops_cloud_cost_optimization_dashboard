"""Reproducible in-process API latency/error benchmark (NFR-1, KPI-5, KPI-6).

    python -m scripts.benchmark_api --out bench.json [--trace trace.csv] [--reps 30]

Runs a fixed, versioned workload (WORKLOAD_VERSION) against the app that is
importable from the current working directory using FastAPI's TestClient
(in-process: no network, no uvicorn).  Only endpoints that also existed in
the pre-change code are used, so the same file can be copied into a checkout
of the baseline commit to produce a like-for-like reference on the same
machine (AC-4).  Latency therefore excludes network and server-framework
overhead outside the ASGI app; see docs/testing-and-acceptance.md.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import sys
import time

WORKLOAD_VERSION = "api-workload-v1"
WORKLOAD = [
    # (name, path, repetitions multiplier)
    ("health", "/", 1.0),
    ("cost_summary", "/api/v1/costs/summary?days=30&use_mock=true", 1.0),
    ("focus_export", "/api/v1/costs/focus-export?days=7&limit=100&use_mock=true", 1.0),
    ("forecast", "/api/v1/forecast/predict?forecast_days=30&use_mock=true", 1.0),
    ("anomalies", "/api/v1/anomalies/detect?days=60&use_mock=true", 0.5),
]


def _pct(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[max(1, math.ceil(p / 100 * len(ordered))) - 1], 2)


def run(reps: int, warmup: int = 2) -> tuple[dict, list[dict]]:
    os.environ.setdefault("FINOP_MOCK_MODE", "true")
    os.environ.setdefault("FINOP_EVIDENCE_DIR", "")
    sys.path.insert(0, os.getcwd())
    from fastapi.testclient import TestClient

    from finops_engine import __version__
    from finops_engine.api.app import app

    trace: list[dict] = []
    per: dict[str, list[float]] = {}
    errors: dict[str, int] = {}
    first_call: dict[str, float] = {}
    started = time.perf_counter()
    with TestClient(app) as client:
        for name, path, mult in WORKLOAD:
            n = max(3, int(reps * mult))
            for i in range(warmup + n):
                t0 = time.perf_counter()
                resp = client.get(path)
                ms = (time.perf_counter() - t0) * 1000
                if i == 0:
                    first_call[name] = round(ms, 2)
                if i < warmup:
                    continue
                per.setdefault(name, []).append(ms)
                if resp.status_code >= 500:
                    errors[name] = errors.get(name, 0) + 1
                trace.append({"seq": len(trace) + 1, "endpoint": name, "status": resp.status_code, "ms": round(ms, 3)})
    wall = time.perf_counter() - started
    all_ms = [t["ms"] for t in trace]
    total_errors = sum(errors.values())
    summary = {
        "workload_version": WORKLOAD_VERSION,
        "app_version": __version__,
        "code_dir_name": os.path.basename(os.getcwd()),
        "reps": reps,
        "warmup_per_endpoint": warmup,
        "requests": len(trace),
        "server_errors": total_errors,
        "server_error_rate": round(total_errors / len(trace), 4) if trace else None,
        "p50_ms": _pct(all_ms, 50),
        "p95_ms": _pct(all_ms, 95),
        "p99_ms": _pct(all_ms, 99),
        "throughput_rps_sequential": round(len(trace) / sum(all_ms) * 1000, 2) if all_ms else None,
        "wall_seconds_including_warmup": round(wall, 2),
        "first_call_ms": first_call,
        "per_endpoint": {
            name: {"n": len(v), "p50_ms": _pct(v, 50), "p95_ms": _pct(v, 95), "max_ms": round(max(v), 2)}
            for name, v in per.items()
        },
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "cpu_count": os.cpu_count(),
            "machine": platform.machine(),
        },
    }
    try:
        import resource

        summary["environment"]["max_rss_mb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)
    except (ImportError, AttributeError):  # Windows
        summary["environment"]["max_rss_mb"] = None
    return summary, trace


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True)
    parser.add_argument("--trace")
    parser.add_argument("--reps", type=int, default=30)
    args = parser.parse_args()
    summary, trace = run(args.reps)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)
    if args.trace:
        with open(args.trace, "w", encoding="utf-8") as fh:
            fh.write("seq,endpoint,status,ms\n")
            for t in trace:
                fh.write(f"{t['seq']},{t['endpoint']},{t['status']},{t['ms']}\n")
    print(json.dumps({k: summary[k] for k in ("requests", "p50_ms", "p95_ms", "server_error_rate")}))


if __name__ == "__main__":
    main()
