"""Component health, degraded/safe mode and recovery evidence (FR-8, NFR-6).

Every component reports successes and failures here.  The registry never
reports ``ok`` for a component it has not heard from: unknown stays
``unknown`` (NT-5).  Status transitions are recorded as events; a transition
from failed/degraded back to ok is a *recovery* with a measured duration.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger("finops.health")


class Status(str, Enum):
    OK = "ok"
    DEGRADED = "degraded"
    FAILED = "failed"
    UNKNOWN = "unknown"


class Mode(str, Enum):
    NORMAL = "normal"
    DEGRADED = "degraded"
    SAFE = "safe"  # data integrity in doubt: recommendations are suppressed


@dataclass
class ComponentState:
    name: str
    status: Status = Status.UNKNOWN
    detail: str = "no signal received yet"
    last_success_ts: float | None = None
    last_failure_ts: float | None = None
    consecutive_failures: int = 0
    failure_started_ts: float | None = None
    expected_interval_s: float | None = None

    def to_dict(self, now: float, stale_factor: float) -> dict[str, Any]:
        status = self.status
        detail = self.detail
        if (
            status == Status.OK
            and self.expected_interval_s
            and self.last_success_ts is not None
            and now - self.last_success_ts > stale_factor * self.expected_interval_s
        ):
            status = Status.UNKNOWN
            detail = f"telemetry stale: last success {round(now - self.last_success_ts, 1)}s ago"
        return {
            "name": self.name,
            "status": status.value,
            "detail": detail,
            "last_success": _iso(self.last_success_ts),
            "last_failure": _iso(self.last_failure_ts),
            "consecutive_failures": self.consecutive_failures,
        }


def _iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts is not None else None


@dataclass
class HealthRegistry:
    stale_factor: float = 3.0
    evidence_dir: Path | None = None
    _components: dict[str, ComponentState] = field(default_factory=dict)
    _events: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=500))
    _safe_mode_reasons: dict[str, str] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    recoveries: int = 0
    failures_observed: int = 0

    def register(self, name: str, expected_interval_s: float | None = None) -> None:
        with self._lock:
            state = self._components.setdefault(name, ComponentState(name=name))
            if expected_interval_s is not None:
                state.expected_interval_s = expected_interval_s

    def record_success(self, name: str, detail: str = "ok", degraded: bool = False) -> None:
        now = time.time()
        with self._lock:
            state = self._components.setdefault(name, ComponentState(name=name))
            previous = state.status
            new_status = Status.DEGRADED if degraded else Status.OK
            if previous in (Status.FAILED, Status.DEGRADED) and new_status == Status.OK:
                duration = now - (state.failure_started_ts or now)
                self.recoveries += 1
                self._event(name, "recovered", f"recovered after {duration:.3f}s", recovery_seconds=round(duration, 3))
                state.failure_started_ts = None
            elif previous != new_status:
                self._event(name, new_status.value, detail)
            state.status = new_status
            state.detail = detail
            state.last_success_ts = now
            state.consecutive_failures = 0 if not degraded else state.consecutive_failures

    def record_failure(self, name: str, detail: str, degraded_only: bool = False) -> None:
        now = time.time()
        with self._lock:
            state = self._components.setdefault(name, ComponentState(name=name))
            new_status = Status.DEGRADED if degraded_only else Status.FAILED
            if state.status not in (Status.FAILED, Status.DEGRADED):
                state.failure_started_ts = now
                self.failures_observed += 1
            if state.status != new_status:
                self._event(name, new_status.value, detail)
            state.status = new_status
            state.detail = detail[:500]
            state.last_failure_ts = now
            state.consecutive_failures += 1

    def enter_safe_mode(self, key: str, reason: str) -> None:
        with self._lock:
            if key not in self._safe_mode_reasons:
                self._event("mode", "safe_mode_entered", reason)
            self._safe_mode_reasons[key] = reason

    def clear_safe_mode(self, key: str) -> None:
        with self._lock:
            if self._safe_mode_reasons.pop(key, None) is not None:
                self._event("mode", "safe_mode_cleared", key)

    @property
    def safe_mode(self) -> bool:
        return bool(self._safe_mode_reasons)

    def component_status(self, name: str) -> str:
        now = time.time()
        with self._lock:
            state = self._components.get(name)
            if state is None:
                return Status.UNKNOWN.value
            return str(state.to_dict(now, self.stale_factor)["status"])

    def snapshot(self) -> dict[str, Any]:
        now = time.time()
        with self._lock:
            components = [s.to_dict(now, self.stale_factor) for s in self._components.values()]
            events = list(self._events)[-50:]
            safe = dict(self._safe_mode_reasons)
        statuses = {c["status"] for c in components}
        if safe:
            mode = Mode.SAFE
        elif statuses & {Status.FAILED.value, Status.DEGRADED.value, Status.UNKNOWN.value}:
            mode = Mode.DEGRADED
        else:
            mode = Mode.NORMAL
        if not components:
            overall = Status.UNKNOWN.value
        elif Status.FAILED.value in statuses:
            overall = Status.FAILED.value
        elif statuses & {Status.DEGRADED.value, Status.UNKNOWN.value} or safe:
            overall = Status.DEGRADED.value
        else:
            overall = Status.OK.value
        return {
            "overall_status": overall,
            "mode": mode.value,
            "safe_mode_reasons": safe,
            "components": sorted(components, key=lambda c: c["name"]),
            "recent_events": events,
            "recoveries_total": self.recoveries,
            "failures_observed_total": self.failures_observed,
        }

    def _event(self, component: str, kind: str, detail: str, **extra: Any) -> None:
        event = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "component": component,
            "event": kind,
            "detail": detail[:500],
            **extra,
        }
        self._events.append(event)
        log = logger.warning if kind in ("failed", "degraded", "safe_mode_entered") else logger.info
        log("health event component=%s event=%s detail=%s", component, kind, event["detail"])
        if self.evidence_dir is not None:
            try:
                self.evidence_dir.mkdir(parents=True, exist_ok=True)
                with (self.evidence_dir / "health-events.jsonl").open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(event) + "\n")
            except OSError:  # evidence is best-effort; never break the request path
                logger.debug("could not persist health event", exc_info=True)

    def reset(self) -> None:
        """Test helper: forget all state."""
        with self._lock:
            self._components.clear()
            self._events.clear()
            self._safe_mode_reasons.clear()
            self.recoveries = 0
            self.failures_observed = 0


def _evidence_dir() -> Path | None:
    raw = os.getenv("FINOP_EVIDENCE_DIR", "output/evidence")
    return Path(raw) if raw else None


health_registry = HealthRegistry(evidence_dir=_evidence_dir())
