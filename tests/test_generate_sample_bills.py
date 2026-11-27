"""The bill generator reproduces the instructor-approved sample data and prices rupee bills correctly."""

from pathlib import Path

import pytest

from scripts import generate_sample_bills as gen

DEMO = Path(__file__).resolve().parents[1] / "data" / "demo"


@pytest.mark.parametrize("scenario", list(gen.SCENARIOS))
def test_default_generation_reproduces_approved_files(scenario, tmp_path):
    name, df = gen.generate(scenario)
    path = tmp_path / name
    df.to_csv(path, index=False, lineterminator="\n")
    expected = (DEMO / name).read_bytes().replace(b"\r\n", b"\n")
    assert path.read_bytes() == expected


def test_inr_bills_scale_prices_not_usage():
    _, usd = gen.generate("may-jun")
    _, inr = gen.generate("may-jun", currency="INR", rate=94.95)
    assert set(inr["BillingCurrency"]) == {"INR"}
    assert (inr["UsageQuantity"] == usd["UsageQuantity"]).all()
    assert (inr["ResourceUtilizationPercent"] == usd["ResourceUtilizationPercent"]).all()
    assert inr["BilledCost"].sum() == pytest.approx(usd["BilledCost"].sum() * 94.95, rel=1e-6)


def test_seed_gives_a_new_variation_with_the_same_planted_step():
    _, a = gen.generate("may-jun")
    _, b = gen.generate("may-jun", seed=7)
    assert not (a["ResourceUtilizationPercent"] == b["ResourceUtilizationPercent"]).all()
    gcp = b[(b["ServiceName"] == "GCP_Compute")].set_index("ChargePeriodStart")["BilledCost"]
    assert gcp["2026-06-09T00:00:00Z"] == pytest.approx(3.6) and gcp["2026-06-10T00:00:00Z"] == pytest.approx(7.2)


def test_cli_accepts_lowercase_currency_and_events(tmp_path, monkeypatch, capsys):
    import sys

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "gen",
            "--period",
            "jan",
            "--year",
            "2026",
            "--currency",
            "inr",
            "--events",
            "Demo",
            "--out-dir",
            str(tmp_path),
        ],
    )
    gen.main()
    (bill,) = tmp_path.glob("*.csv")
    assert bill.name.endswith("_inr.csv")
    assert "INR" in bill.read_text().splitlines()[1]
