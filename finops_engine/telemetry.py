"""In-process request telemetry used for SLO reporting (NFR-1, NFR-3, NFR-6).

Prometheus histograms are the long-term source of truth; this bounded ring
buffer lets ``/api/v1/ops/slo`` report recent p50/p95/p99 latency and error
rate without an external Prometheus server.  Values are labelled with the
sample count so a "good" number from 3 requests is not mistaken for evidence.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from typing import Any


def percentile(values: list[float], pct: float) -> float | None:
    """Nearest-rank percentile (pct in 0..100)."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100.0 * len(ordered)))
    return ordered[rank - 1]


class RequestStats:
    def __init__(self, maxlen: int = 10_000) -> None:
        self._samples: deque[tuple[float, str, float, int]] = deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self.started = time.time()

    def record(self, route: str, duration_s: float, status_code: int) -> None:
        with self._lock:
            self._samples.append((time.time(), route, duration_s, status_code))

    def summary(self, window_s: float | None = None, exclude_routes: tuple[str, ...] = ()) -> dict[str, Any]:
        now = time.time()
        with self._lock:
            samples = [
                s for s in self._samples if (window_s is None or now - s[0] <= window_s) and s[1] not in exclude_routes
            ]
        durations_ms = [s[2] * 1000 for s in samples]
        errors = sum(1 for s in samples if s[3] >= 500)
        client_errors = sum(1 for s in samples if 400 <= s[3] < 500)
        span = (max(s[0] for s in samples) - min(s[0] for s in samples)) if len(samples) > 1 else None
        by_route: dict[str, list[float]] = {}
        for s in samples:
            by_route.setdefault(s[1], []).append(s[2] * 1000)
        return {
            "sample_count": len(samples),
            "p50_ms": _r(percentile(durations_ms, 50)),
            "p95_ms": _r(percentile(durations_ms, 95)),
            "p99_ms": _r(percentile(durations_ms, 99)),
            "server_error_rate": round(errors / len(samples), 4) if samples else None,
            "client_error_count": client_errors,
            "throughput_rps": round(len(samples) / span, 2) if span else None,
            "by_route_p95_ms": {route: _r(percentile(v, 95)) for route, v in sorted(by_route.items())},
        }

    def reset(self) -> None:
        with self._lock:
            self._samples.clear()


def _r(value: float | None) -> float | None:
    return round(value, 2) if value is not None else None


request_stats = RequestStats()
