"""The sample-data report runs end to end on the bundled, instructor-approved sample data."""

from pathlib import Path

import pytest

from scripts import sample_data_report as rep

DEMO = Path(__file__).resolve().parents[1] / "data" / "demo"


@pytest.fixture(autouse=True)
def _sample_config(monkeypatch):
    from finops_engine import engine_config

    monkeypatch.setenv("FINOP_ENGINE_CONFIG", str(rep.SAMPLE_CONFIG))
    engine_config.get_engine_config.cache_clear()
    yield
    monkeypatch.delenv("FINOP_ENGINE_CONFIG")
    engine_config.get_engine_config.cache_clear()


def test_step_scenario_is_detected_and_counted_as_true_positive():
    result = rep.analyse_file(DEMO / "focus_bill_may_june.csv")
    assert result["ingestion"]["rows_accepted"] == 549 and result["ingestion"]["rows_quarantined"] == 0
    assert [(a["date"], a["service"]) for a in result["anomalies"]] == [("2026-06-10", "GCP_Compute")]
    ev = result["anomaly_evaluation"]
    assert ev["true_positive_alerts"] == 1 and ev["false_positive_alerts"] == 0
    assert ev["point_events_detected"] == ["GCP compute doubles overnight"]


def test_q2_reference_kpi1_and_rendering(tmp_path):
    q2 = rep.analyse_file(DEMO / rep.Q2_FILE)
    assert q2["overview"]["total_cost_usd"] == pytest.approx(11900.3938 * 94.95, abs=0.05)  # INR
    assert q2["ingestion"]["warnings"] == {}  # sample config accepts GB-Month/GB-Seconds/Invocations
    kpis = {k["kpi"].split()[0]: k for k in rep.kpi_table([q2])}
    assert kpis["KPI-1"]["result"] == "MET"
    report = {
        "generated_at": "t",
        "app_version": "x",
        "config_version": q2["config_version"],
        "kpis": list(kpis.values()),
        "files": [q2],
    }
    page = rep.render_html(report)
    assert "GENERATED sample data" in page and "KPI-1" in page and "<svg" in page
    assert "GENERATED sample data" in rep.render_md(report)
