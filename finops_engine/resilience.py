"""Bounded timeouts, retries, circuit breaking and saturation guards (FR-8, NFR-4, NT-4).

``call_with_resilience`` runs a dependency call in a bounded worker pool:

* each attempt has a hard wall-clock ``timeout_s`` (the caller is released;
  the worker thread finishes in the background and its result is discarded)
* at most ``1 + retries`` attempts, with linear back-off
* a per-dependency circuit breaker fails fast after repeated failures and
  half-opens after ``reset_s`` to probe for recovery
* if every worker is busy the call fails fast with ``SaturatedError`` instead
  of queueing without bound

Every outcome is reported to the health registry, so failures are visible
and recoveries are recorded with their duration.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from typing import TypeVar

from finops_engine.errors import ConnectorError
from finops_engine.health import HealthRegistry, health_registry

logger = logging.getLogger("finops.resilience")

T = TypeVar("T")

MAX_WORKERS = 8


class DependencyTimeoutError(ConnectorError):
    """A dependency call exceeded its time budget on every attempt."""


class CircuitOpenError(ConnectorError):
    """The circuit breaker is open: the dependency is not being called."""


class SaturatedError(ConnectorError):
    """All dependency workers are busy; the call was rejected immediately."""


@dataclass
class CircuitBreaker:
    failure_threshold: int = 3
    reset_s: float = 60.0
    state: str = "closed"
    failures: int = 0
    opened_at: float | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def allow(self) -> bool:
        with self._lock:
            if self.state == "open":
                if self.opened_at is not None and time.monotonic() - self.opened_at >= self.reset_s:
                    self.state = "half_open"
                    return True
                return False
            return True

    def on_success(self) -> None:
        with self._lock:
            self.state = "closed"
            self.failures = 0
            self.opened_at = None

    def on_failure(self) -> None:
        with self._lock:
            self.failures += 1
            if self.state == "half_open" or self.failures >= self.failure_threshold:
                self.state = "open"
                self.opened_at = time.monotonic()


_breakers: dict[str, CircuitBreaker] = {}
_breakers_lock = threading.Lock()
_executor = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="finops-dep")
_in_flight = 0
_in_flight_lock = threading.Lock()


def get_breaker(name: str, failure_threshold: int = 3, reset_s: float = 60.0) -> CircuitBreaker:
    with _breakers_lock:
        breaker = _breakers.get(name)
        if breaker is None:
            breaker = CircuitBreaker(failure_threshold=failure_threshold, reset_s=reset_s)
            _breakers[name] = breaker
        return breaker


def in_flight() -> int:
    """Number of dependency calls currently occupying a worker (including timed-out ones still running)."""
    with _in_flight_lock:
        return _in_flight


def reset_breakers() -> None:
    with _breakers_lock:
        _breakers.clear()


def breaker_states() -> dict[str, str]:
    with _breakers_lock:
        return {name: b.state for name, b in _breakers.items()}


def _submit(fn: Callable[[], T]):  # type: ignore[no-untyped-def]
    global _in_flight
    with _in_flight_lock:
        if _in_flight >= MAX_WORKERS:
            raise SaturatedError(f"all {MAX_WORKERS} dependency workers are busy")
        _in_flight += 1

    def _run() -> T:
        global _in_flight
        try:
            return fn()
        finally:
            with _in_flight_lock:
                _in_flight -= 1

    return _executor.submit(_run)


def call_with_resilience(
    name: str,
    fn: Callable[[], T],
    *,
    timeout_s: float,
    retries: int,
    backoff_s: float = 0.0,
    breaker: CircuitBreaker | None = None,
    health: HealthRegistry | None = None,
    retry_on: tuple[type[BaseException], ...] = (Exception,),
) -> tuple[T, dict[str, object]]:
    """Run ``fn`` with bounded time/attempts. Returns ``(result, attempt_log)``."""
    registry = health or health_registry
    breaker = breaker or get_breaker(name)
    attempts: list[dict[str, object]] = []
    if not breaker.allow():
        registry.record_failure(name, "circuit open: failing fast")
        raise CircuitOpenError(f"{name}: circuit breaker open")

    last_error: BaseException | None = None
    for attempt in range(1, retries + 2):
        started = time.monotonic()
        try:
            future = _submit(fn)
        except SaturatedError as exc:
            attempts.append({"attempt": attempt, "outcome": "saturated"})
            registry.record_failure(name, str(exc), degraded_only=True)
            raise
        try:
            result = future.result(timeout=timeout_s)
        except FutureTimeout:
            last_error = DependencyTimeoutError(f"{name}: attempt {attempt} exceeded {timeout_s}s")
            attempts.append({"attempt": attempt, "outcome": "timeout", "elapsed_s": round(timeout_s, 3)})
        except retry_on as exc:  # noqa: B030 - tuple of exception types is intended
            last_error = exc
            attempts.append(
                {
                    "attempt": attempt,
                    "outcome": "error",
                    "error": type(exc).__name__,
                    "elapsed_s": round(time.monotonic() - started, 3),
                }
            )
        else:
            attempts.append({"attempt": attempt, "outcome": "ok", "elapsed_s": round(time.monotonic() - started, 3)})
            breaker.on_success()
            registry.record_success(name, f"ok after {attempt} attempt(s)")
            return result, {"attempts": attempts, "breaker": breaker.state}
        logger.warning("dependency %s attempt %d failed: %s", name, attempt, last_error)
        if attempt <= retries and backoff_s:
            time.sleep(backoff_s * attempt)

    breaker.on_failure()
    registry.record_failure(name, f"failed after {len(attempts)} attempt(s): {last_error}")
    if isinstance(last_error, ConnectorError):
        raise last_error
    raise ConnectorError(f"{name}: failed after {len(attempts)} attempt(s): {last_error}") from last_error
