"""Anomaly detection over FOCUS cost telemetry.

Flags days whose spend deviates sharply from a provider/service baseline,
using Isolation Forest combined with a rolling z-score check.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta, timezone
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

from finops_engine.currency import format_money
from finops_engine.engine_config import AnomalyConfig
from finops_engine.schema.focus_spec import FocusRecord, normalize_to_focus_dataframe

DETECTION_METHOD = "isolation_forest+rolling_zscore"
# Baseline treated as flat when its std is at most this fraction of its mean.
FLAT_BASELINE_REL_STD = 0.01


class AnomalyDetector:
    """Statistical and unsupervised machine learning detector for cloud spend anomalies.

    A day is flagged only when *both* signals agree: the per-(provider, service)
    Isolation Forest marks it as an outlier **and** its z-score against the
    preceding ``baseline_window_days`` (excluding the day itself) is at least
    ``z_threshold``.  Each alert carries the evidence used to raise it.
    """

    def __init__(
        self,
        contamination: float = 0.05,
        z_threshold: float = 2.5,
        baseline_window_days: int = 7,
        min_baseline_points: int = 3,
        severity_high_excess_usd: float = 200.0,
        severity_medium_excess_usd: float = 50.0,
        config_version: str = "unversioned",
        min_relative_excess: float = 0.2,
        min_absolute_excess_usd: float = 5.0,
        currency: str = "USD",
    ):
        self.contamination = contamination
        self.z_threshold = z_threshold
        self.baseline_window_days = baseline_window_days
        self.min_baseline_points = min_baseline_points
        self.severity_high_excess_usd = severity_high_excess_usd
        self.severity_medium_excess_usd = severity_medium_excess_usd
        self.config_version = config_version
        # Materiality gate: spend must also exceed the baseline by this fraction,
        # so statistically "significant" but immaterial wiggles on very stable
        # series are not raised as alerts.
        self.min_relative_excess = min_relative_excess
        self.min_absolute_excess_usd = min_absolute_excess_usd
        # Amount thresholds (``*_usd``) and alert values are in this reporting currency.
        self.currency = currency.upper()

    @classmethod
    def from_config(
        cls,
        config: AnomalyConfig,
        config_version: str,
        contamination: float | None = None,
        currency: str | None = None,
    ) -> AnomalyDetector:
        if currency is None:
            from finops_engine.engine_config import get_engine_config

            currency = get_engine_config().ingestion.base_currency
        return cls(
            contamination=contamination if contamination is not None else config.contamination,
            z_threshold=config.z_threshold,
            baseline_window_days=config.baseline_window_days,
            min_baseline_points=config.min_baseline_points,
            severity_high_excess_usd=config.severity_high_excess_usd,
            severity_medium_excess_usd=config.severity_medium_excess_usd,
            config_version=config_version,
            min_relative_excess=config.min_relative_excess,
            min_absolute_excess_usd=config.min_absolute_excess_usd,
            currency=currency,
        )

    def _severity(self, excess: float) -> str:
        if excess > self.severity_high_excess_usd:
            return "HIGH"
        if excess > self.severity_medium_excess_usd:
            return "MEDIUM"
        return "LOW"

    def detect_anomalies(self, records: list[FocusRecord]) -> list[dict[str, Any]]:
        """Analyzes FOCUS records for spend anomalies and returns detailed root-cause insights."""
        df = normalize_to_focus_dataframe(records)
        if df.empty:
            return []

        df["date"] = pd.to_datetime(df["billing_period_start"], utc=True).dt.date
        accounts = df.groupby(["provider_name", "service_name"])["sub_account_id"].agg(lambda x: sorted(set(x)))
        daily = df.groupby(["date", "provider_name", "service_name"])["billed_cost"].sum().reset_index()
        daily = daily.sort_values(["provider_name", "service_name", "date"]).reset_index(drop=True)

        if len(daily) < 5:
            return []

        # ── Per-group Isolation Forest ──────────────────────────────────────
        # Training on the full dataset mixes cost scales across providers and
        # services, making the outlier threshold meaningless.  Instead we fit
        # a fresh model for each (provider, service) pair so that anomaly
        # detection operates within a homogeneous cost population.
        iforest_flags: list[int] = []
        for (_provider, _service), grp in daily.groupby(["provider_name", "service_name"]):
            if len(grp) < 5:
                # Not enough observations for Isolation Forest; mark as normal.
                iforest_flags.extend([1] * len(grp))
                continue
            model = IsolationForest(contamination=self.contamination, random_state=42)
            flags = model.fit_predict(grp[["billed_cost"]].values)
            iforest_flags.extend(flags.tolist())

        daily["iforest_score"] = iforest_flags

        window, min_points = self.baseline_window_days, self.min_baseline_points
        daily["mean"] = daily.groupby(["provider_name", "service_name"])["billed_cost"].transform(
            lambda x: x.shift(1).rolling(window, min_periods=min_points).mean()
        )
        daily["std"] = daily.groupby(["provider_name", "service_name"])["billed_cost"].transform(
            lambda x: x.shift(1).rolling(window, min_periods=min_points).std()
        )
        daily["points"] = daily.groupby(["provider_name", "service_name"])["billed_cost"].transform(
            lambda x: x.shift(1).rolling(window, min_periods=1).count()
        )

        # A flat baseline (std ~ 0, typical of fixed-rate or reserved resources) has no spread to
        # score against: any material jump off it is anomalous. Previously z was forced to 0 there,
        # so a step change such as a cluster doubling overnight could never alert. Isolation Forest
        # also cannot flag it once the new level persists (the new level stops being rare).
        std_safe = daily["std"].fillna(0)
        flat = daily["mean"].notna() & (std_safe <= FLAT_BASELINE_REL_STD * daily["mean"].abs())
        daily["flat_baseline"] = flat
        # Score against a spread floor so z stays finite (and JSON-serialisable) on flat baselines.
        spread = np.maximum(std_safe, np.maximum(FLAT_BASELINE_REL_STD * daily["mean"].abs(), 0.01))
        daily["z_score"] = ((daily["billed_cost"] - daily["mean"]) / spread).fillna(0.0)

        anomalies_df = daily[
            daily["mean"].notna()
            & ((daily["iforest_score"] == -1) | daily["flat_baseline"])
            & (daily["z_score"] >= self.z_threshold)
            & (daily["billed_cost"] >= daily["mean"] * (1.0 + self.min_relative_excess))
            & (daily["billed_cost"] - daily["mean"] >= self.min_absolute_excess_usd)
        ].copy()

        def money(v: float) -> str:
            return format_money(v, self.currency)

        results = []
        for _, row in anomalies_df.iterrows():
            expected_cost = float(row["mean"])
            actual_cost = float(row["billed_cost"])
            excess_cost = round(actual_cost - expected_cost, 2)
            day: date = row["date"]
            provider, service = str(row["provider_name"]), str(row["service_name"])
            z = round(float(row["z_score"]), 2)
            window_start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
            alert_key = f"{day.isoformat()}|{provider}|{service}"

            results.append(
                {
                    # --- original contract (unchanged) ---
                    "date": str(day),
                    "provider": provider,
                    "service": service,
                    "actual_cost_usd": round(actual_cost, 2),
                    "expected_baseline_usd": round(expected_cost, 2),
                    "anomaly_excess_usd": excess_cost,
                    "z_score": z,
                    "severity": self._severity(excess_cost),
                    "root_cause": f"{provider} {service} spent {money(excess_cost)} above baseline",
                    # --- extended alert contract (FR-3) ---
                    "alert_id": "anom-" + hashlib.sha256(alert_key.encode()).hexdigest()[:12],
                    "status": "open",
                    "affected_entity": {
                        "provider": provider,
                        "service": service,
                        "accounts": accounts.get((provider, service), []),
                    },
                    "window_start": window_start.isoformat(),
                    "window_end": (window_start + timedelta(days=1)).isoformat(),
                    "observed_cost_usd": round(actual_cost, 2),
                    "expected_cost_usd": round(expected_cost, 2),
                    "score": z,
                    "reason": (
                        f"Daily spend {money(actual_cost)} jumped off a flat "
                        f"{self.baseline_window_days}-day baseline of {money(expected_cost)}/day "
                        f"(+{excess_cost / expected_cost:.0%})"
                        if row["flat_baseline"] and expected_cost
                        else f"Daily spend {money(actual_cost)} is {z} standard deviations above the "
                        f"{self.baseline_window_days}-day baseline mean {money(expected_cost)} and was "
                        "flagged as an outlier by Isolation Forest"
                    ),
                    "detection_method": DETECTION_METHOD,
                    "evidence": {
                        "baseline_window_days": self.baseline_window_days,
                        "baseline_points": int(row["points"]),
                        "baseline_mean_usd": round(expected_cost, 2),
                        "baseline_std_usd": round(float(row["std"]), 2),
                        "isolation_forest_outlier": bool(row["iforest_score"] == -1),
                        "flat_baseline": bool(row["flat_baseline"]),
                        "z_threshold": self.z_threshold,
                        "contamination": self.contamination,
                        "min_relative_excess": self.min_relative_excess,
                        "min_absolute_excess_usd": self.min_absolute_excess_usd,
                        "relative_excess": round(excess_cost / expected_cost, 4) if expected_cost else None,
                    },
                    "config_version": self.config_version,
                    "currency": self.currency,
                }
            )

        return sorted(results, key=lambda x: x["anomaly_excess_usd"], reverse=True)


# --------------------------------------------------------------------------
# Result memoisation.  Fitting one Isolation Forest per series costs ~1.5 s
# per call on a 2-vCPU host; dashboards and the metrics loop ask the same
# question repeatedly.  Results are cached for a short TTL keyed by a cheap
# fingerprint of the input (record count, cost sum, time span, source) and
# every detector parameter, so any data or config change misses the cache.
# --------------------------------------------------------------------------
import threading as _threading  # noqa: E402

from cachetools import TTLCache as _TTLCache  # noqa: E402

_DETECTION_CACHE: _TTLCache = _TTLCache(maxsize=32, ttl=300)
_DETECTION_LOCK = _threading.Lock()


def _fingerprint(records: list[FocusRecord], source: str) -> tuple:
    if not records:
        return (source, 0)
    starts = [r.billing_period_start for r in records]
    return (
        source,
        len(records),
        round(sum(r.billed_cost for r in records), 6),
        min(starts).isoformat(),
        max(starts).isoformat(),
    )


def detect_anomalies_cached(detector: AnomalyDetector, records: list[FocusRecord], source: str) -> list[dict[str, Any]]:
    params = (
        detector.contamination,
        detector.z_threshold,
        detector.baseline_window_days,
        detector.min_baseline_points,
        detector.min_relative_excess,
        detector.min_absolute_excess_usd,
        detector.severity_high_excess_usd,
        detector.severity_medium_excess_usd,
        detector.config_version,
        detector.currency,
    )
    key = (_fingerprint(records, source), params)
    with _DETECTION_LOCK:
        hit = _DETECTION_CACHE.get(key)
    if hit is not None:
        return [dict(a) for a in hit]
    result = detector.detect_anomalies(records)
    with _DETECTION_LOCK:
        _DETECTION_CACHE[key] = result
    return [dict(a) for a in result]


def clear_detection_cache() -> None:
    with _DETECTION_LOCK:
        _DETECTION_CACHE.clear()
