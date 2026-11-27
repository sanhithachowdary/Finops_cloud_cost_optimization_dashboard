"""FR-3 / KPI-2 / KPI-3: anomaly alert contract, precision vs ground truth, forecast back-test."""

import json

import pytest

from finops_engine.ai import AnomalyDetector, CostForecaster
from finops_engine.engine_config import EngineConfig
from finops_engine.evaluation import backtest_forecast, evaluate_anomaly_detector
from finops_engine.ingestion import ingestion_store
from tests.conftest import FIXTURES, ingest_file


def _labels(name):
    meta = json.loads((FIXTURES / name).read_text())
    return {(x["date"], x["provider"], x["service"]) for x in meta["labels"]}


@pytest.fixture
def bench():
    ingest_file(FIXTURES / "billing_benchmark_v1.csv")
    return ingestion_store.snapshot_records()


def test_alert_contract_fields(bench):
    cfg = EngineConfig()
    alerts = AnomalyDetector.from_config(cfg.anomaly, cfg.version).detect_anomalies(bench)
    assert alerts
    for a in alerts:
        for key in (
            "alert_id",
            "status",
            "severity",
            "affected_entity",
            "observed_cost_usd",
            "expected_cost_usd",
            "score",
            "reason",
            "window_start",
            "window_end",
            "evidence",
            "config_version",
        ):
            assert a[key] is not None, key
        assert a["observed_cost_usd"] > a["expected_cost_usd"]
        assert a["config_version"] == cfg.version


def test_precision_and_recall_on_development_fixture(bench):
    cfg = EngineConfig()
    result = evaluate_anomaly_detector(
        bench, _labels("anomaly_labels_v1.json"), AnomalyDetector.from_config(cfg.anomaly, cfg.version)
    )
    # Recorded in docs/evidence/KPI-results.md; asserted as regression floors.
    assert result["precision"] >= 0.85
    assert result["recall"] >= 0.9


def test_precision_on_independent_holdout_fixture():
    ingest_file(FIXTURES / "billing_holdout_v1.csv")
    cfg = EngineConfig()
    result = evaluate_anomaly_detector(
        ingestion_store.snapshot_records(),
        _labels("anomaly_labels_holdout_v1.json"),
        AnomalyDetector.from_config(cfg.anomaly, cfg.version),
    )
    assert result["precision"] >= 0.85
    assert result["recall"] >= 0.7


def test_detector_is_deterministic(bench):
    a = AnomalyDetector().detect_anomalies(bench)
    b = AnomalyDetector().detect_anomalies(bench)
    assert [x["alert_id"] for x in a] == [x["alert_id"] for x in b]


def test_no_alerts_on_empty_or_tiny_input():
    assert AnomalyDetector().detect_anomalies([]) == []
    result = evaluate_anomaly_detector([], {("2026-01-01", "AWS", "X")}, AnomalyDetector())
    assert result["precision"] is None and result["recall"] == 0.0


def test_backtest_reports_metrics_and_baselines(bench):
    result = backtest_forecast(bench, holdout_days=14)
    assert result["status"] == "ok"
    assert result["holdout_period"] == ["2026-06-17", "2026-06-30"]
    for block in [result["model"], *result["baselines"].values()]:
        assert block["mae_usd"] >= 0 and block["rmse_usd"] >= block["mae_usd"]
        assert block["mape_pct"] is not None
    assert 0 <= result["prediction_interval_coverage"] <= 1


def test_backtest_insufficient_history():
    ingest_file(FIXTURES / "billing_small_v1.csv")
    assert backtest_forecast(ingestion_store.snapshot_records(), 14)["status"] == "insufficient_data"


def test_forecaster_reports_insufficient_history():
    ingest_file(FIXTURES / "billing_small_v1.csv")
    result = CostForecaster(forecast_days=7, min_history_days=14).predict_future_cost(
        ingestion_store.snapshot_records()
    )
    assert result["status"] == "insufficient_data"
    assert result["forecast"] == []


def test_absolute_materiality_floor_suppresses_cent_level_noise():
    # Regression (public FOCUS sample): $0.45 vs $0.00 baselines were alerted.
    from datetime import datetime, timedelta, timezone

    from finops_engine.schema import CloudProvider, FocusRecord

    start = datetime(2026, 6, 1, tzinfo=timezone.utc)

    def rec(day, cost, service):
        return FocusRecord(
            provider_name=CloudProvider.AWS,
            billed_cost=cost,
            effective_cost=cost,
            service_name=service,
            billing_period_start=start + timedelta(days=day),
            billing_period_end=start + timedelta(days=day + 1),
        )

    records = [rec(d, 0.01 + 0.001 * (d % 3), "Tiny") for d in range(30)] + [rec(30, 0.45, "Tiny")]
    records += [rec(d, 100 + (d % 3), "Big") for d in range(30)] + [rec(30, 400.0, "Big")]
    alerts = AnomalyDetector(contamination=0.05).detect_anomalies(records)
    assert {a["service"] for a in alerts} == {"Big"}
    assert AnomalyDetector(min_absolute_excess_usd=0.0).detect_anomalies(records)  # floor is configurable


def _daily(costs, service="Compute", provider=None):
    from datetime import datetime, timedelta, timezone

    from finops_engine.schema import CloudProvider, FocusRecord

    start = datetime(2026, 5, 1, tzinfo=timezone.utc)
    return [
        FocusRecord(
            provider_name=provider or CloudProvider.GCP,
            billed_cost=c,
            effective_cost=c,
            service_name=service,
            billing_period_start=start + timedelta(days=i),
            billing_period_end=start + timedelta(days=i + 1),
        )
        for i, c in enumerate(costs)
    ]


def test_step_change_off_flat_baseline_is_alerted():
    # Regression (user sample focus_bill_may_june.csv): GCP compute doubled overnight from a
    # perfectly flat $3.60/day. std == 0 used to force z = 0, and Isolation Forest cannot flag
    # a level that then persists, so the step was never alerted at any threshold.
    records = _daily([3.6] * 40 + [7.2] * 21)
    alerts = AnomalyDetector(min_absolute_excess_usd=1.0).detect_anomalies(records)
    assert [a["date"] for a in alerts] == ["2026-06-10"]
    alert = alerts[0]
    assert alert["evidence"]["flat_baseline"] is True
    assert alert["expected_cost_usd"] == pytest.approx(3.6)
    assert "flat" in alert["reason"]
    json.dumps(alerts, allow_nan=False)  # z stays finite on a zero-std baseline


def test_flat_baseline_respects_materiality_gates():
    records = _daily([3.6] * 40 + [7.2] * 21)
    # +$3.60/day is below the shipped $5/day floor: suppressed by design (documented).
    assert AnomalyDetector().detect_anomalies(records) == []
    # A 10% wobble off a flat line is below the 20% relative gate.
    assert AnomalyDetector(min_absolute_excess_usd=0.0).detect_anomalies(_daily([10.0] * 30 + [11.0])) == []
    # A flat line that stays flat never alerts.
    assert AnomalyDetector(min_absolute_excess_usd=0.0).detect_anomalies(_daily([10.0] * 40)) == []
