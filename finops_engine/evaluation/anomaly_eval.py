"""Anomaly-detector evaluation against an independent ground-truth oracle (KPI-3).

Ground truth is supplied as a set of ``(date, provider, service)`` labels that
were written by the fixture generator when it injected the anomalies, i.e.
independently of the detector being evaluated.
"""

from __future__ import annotations

from typing import Any

from finops_engine.ai.anomaly_detector import AnomalyDetector
from finops_engine.schema.focus_spec import FocusRecord

Label = tuple[str, str, str]  # (YYYY-MM-DD, provider, service)


def evaluate_anomaly_detector(
    records: list[FocusRecord], ground_truth: set[Label], detector: AnomalyDetector
) -> dict[str, Any]:
    alerts = detector.detect_anomalies(records)
    predicted: set[Label] = {(a["date"], a["provider"], a["service"]) for a in alerts}
    tp = predicted & ground_truth
    fp = predicted - ground_truth
    fn = ground_truth - predicted
    precision = len(tp) / len(predicted) if predicted else None
    recall = len(tp) / len(ground_truth) if ground_truth else None
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision is not None and recall is not None and (precision + recall) > 0
        else None
    )
    return {
        "alerts": len(predicted),
        "ground_truth_positives": len(ground_truth),
        "true_positives": len(tp),
        "false_positives": len(fp),
        "false_negatives": len(fn),
        "precision": round(precision, 4) if precision is not None else None,
        "recall": round(recall, 4) if recall is not None else None,
        "f1": round(f1, 4) if f1 is not None else None,
        "false_positive_labels": sorted(fp),
        "false_negative_labels": sorted(fn),
        "detector": {
            "method": "isolation_forest+rolling_zscore",
            "contamination": detector.contamination,
            "z_threshold": detector.z_threshold,
            "baseline_window_days": detector.baseline_window_days,
            "min_relative_excess": detector.min_relative_excess,
            "config_version": detector.config_version,
        },
        "precision_undefined_reason": None if predicted else "no alerts raised (precision = 0/0)",
    }
