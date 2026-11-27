"""Reporting currency: conversion of connector records, formatting, and API/dashboard wiring."""

from datetime import datetime, timezone

import pytest

from finops_engine.currency import UnsupportedCurrencyError, format_money, to_base_currency
from finops_engine.engine_config import EngineConfig, IngestionConfig
from finops_engine.schema import CloudProvider, FocusRecord

INR = IngestionConfig(base_currency="INR", fx_rates_to_base={"USD": 94.95})


def _rec(cost, currency="USD"):
    t = datetime(2026, 6, 1, tzinfo=timezone.utc)
    return FocusRecord(
        provider_name=CloudProvider.AWS,
        billed_cost=cost,
        effective_cost=cost * 0.9,
        currency=currency,
        service_name="AmazonEC2",
        billing_period_start=t,
        billing_period_end=t,
    )


def test_usd_records_are_converted_to_inr():
    (r,) = to_base_currency([_rec(10.0)], INR)
    assert r.currency == "INR"
    assert r.billed_cost == pytest.approx(949.5)
    assert r.effective_cost == pytest.approx(854.55)


def test_base_currency_records_unchanged_and_missing_rate_rejected():
    rec = _rec(10.0, "INR")
    assert to_base_currency([rec], INR) == [rec]
    with pytest.raises(UnsupportedCurrencyError):
        to_base_currency([_rec(1.0, "EUR")], INR)


@pytest.mark.parametrize(
    "value,code,expected",
    [
        (1234567.891, "INR", "₹12,34,567.89"),
        (999.5, "INR", "₹999.50"),
        (-150000, "INR", "-₹1,50,000.00"),
        (1234567.891, "USD", "$1,234,567.89"),
        (None, "INR", "-"),
    ],
)
def test_format_money(value, code, expected):
    assert format_money(value, code) == expected


def test_billing_export_in_usd_is_ingested_as_inr():
    from finops_engine.ingestion import validate_rows

    row = {
        "ProviderName": "AWS",
        "ServiceName": "AmazonEC2",
        "BilledCost": "2.00",
        "BillingCurrency": "USD",
        "ChargePeriodStart": "2026-06-01T00:00:00Z",
        "ChargePeriodEnd": "2026-06-01T23:59:59Z",
    }
    res = validate_rows([row], INR)
    (rec,) = res.accepted
    assert rec.currency == "INR" and rec.billed_cost == pytest.approx(189.9)


def test_anomaly_reason_uses_reporting_currency():
    from finops_engine.ai import AnomalyDetector

    records = [_rec(c, "INR") for c in [100.0] * 0]
    from datetime import timedelta

    t = datetime(2026, 5, 1, tzinfo=timezone.utc)
    records = [
        FocusRecord(
            provider_name=CloudProvider.GCP,
            billed_cost=c,
            effective_cost=c,
            currency="INR",
            service_name="Compute",
            billing_period_start=t + timedelta(days=i),
            billing_period_end=t + timedelta(days=i + 1),
        )
        for i, c in enumerate([342.0] * 40 + [684.0] * 5)
    ]
    alerts = AnomalyDetector(min_absolute_excess_usd=95, currency="INR").detect_anomalies(records)
    assert alerts and alerts[0]["currency"] == "INR"
    assert "₹684.00" in alerts[0]["reason"] and "$" not in alerts[0]["reason"]


def test_mock_connector_and_healthz_report_in_configured_currency(monkeypatch, tmp_path):
    import json

    from fastapi.testclient import TestClient

    from finops_engine import engine_config
    from finops_engine.api.app import app
    from finops_engine.connectors import fetch_multicloud_cost

    usd_total = sum(r.billed_cost for r in fetch_multicloud_cost(use_mock=True, days=10)[0])
    cfg = EngineConfig().model_dump(mode="json")
    cfg["ingestion"]["base_currency"] = "INR"
    cfg["ingestion"]["fx_rates_to_base"] = {"USD": 94.95}
    path = tmp_path / "inr.json"
    path.write_text(json.dumps(cfg))
    monkeypatch.setenv("FINOP_ENGINE_CONFIG", str(path))
    engine_config.get_engine_config.cache_clear()
    fetch_multicloud_cost.cache_clear()
    try:
        records, _ = fetch_multicloud_cost(use_mock=True, days=10)
        assert {r.currency for r in records} == {"INR"}
        assert sum(r.billed_cost for r in records) == pytest.approx(usd_total * 94.95, rel=1e-6)
        assert TestClient(app).get("/healthz").json()["currency"] == "INR"
    finally:
        monkeypatch.undo()
        engine_config.get_engine_config.cache_clear()
        fetch_multicloud_cost.cache_clear()
