"""FR-2: spend analytics verified against hand-computed expected results.

The oracle (``billing_small_v1.expected.json``) was computed by hand from the
CSV, not by running the engine.  A second, pure-Python recomputation over the
large benchmark fixture checks the pandas implementation independently.
"""

import csv
import json
from collections import defaultdict
from datetime import datetime, timezone

import pytest

from finops_engine.analytics import period_comparison, spend_breakdown, spend_overview, spend_trend
from finops_engine.ingestion import ingestion_store
from tests.conftest import FIXTURES, ingest_file


@pytest.fixture
def small_records():
    report = ingest_file(FIXTURES / "billing_small_v1.csv")
    assert report["rows_quarantined"] == 0
    return ingestion_store.snapshot_records()


@pytest.fixture
def expected():
    return json.loads((FIXTURES / "billing_small_v1.expected.json").read_text())


def _as_map(items, key="key", value="cost_usd"):
    return {i[key]: i[value] for i in items}


def test_totals_match_hand_computed(small_records, expected):
    overview = spend_overview(small_records)
    assert overview["total_cost_usd"] == pytest.approx(expected["total_billed_usd"])
    assert spend_overview(small_records, "effective_cost")["total_cost_usd"] == pytest.approx(
        expected["total_effective_usd"]
    )
    assert overview["by_charge_category"] == pytest.approx(expected["by_charge_category"])


@pytest.mark.parametrize(
    "dimension,key", [("provider", "by_provider"), ("service", "by_service"), ("account", "by_account")]
)
def test_breakdowns_match_hand_computed(small_records, expected, dimension, key):
    result = spend_breakdown(small_records, dimension)
    assert _as_map(result["items"]) == pytest.approx(expected[key])
    assert result["total_cost_usd"] == pytest.approx(expected["total_billed_usd"])


@pytest.mark.parametrize("granularity", ["daily", "weekly", "monthly"])
def test_trends_match_hand_computed_utc(small_records, expected, granularity):
    series = spend_trend(small_records, granularity)
    assert {s["period"]: s["cost_usd"] for s in series} == pytest.approx(expected[granularity])
    assert sum(s["cost_usd"] for s in series) == pytest.approx(expected["total_billed_usd"])


def test_timezone_boundary_row_lands_on_utc_day(small_records):
    # 2026-05-31T23:30-02:00 is 2026-06-01T01:30Z -> June, not May.
    days = {s["period"] for s in spend_trend(small_records, "daily")}
    assert "2026-05-31" not in days
    assert "2026-06-01" in days


def test_breakdown_top_n_folds_rest_into_other(small_records):
    result = spend_breakdown(small_records, "service", top_n=2)
    assert len(result["items"]) == 2
    assert result["other"]["members"] == 2
    folded = sum(i["cost_usd"] for i in result["items"]) + result["other"]["cost_usd"]
    assert folded == pytest.approx(425.50)


def test_period_comparison():
    ingest_file(FIXTURES / "billing_small_v1.csv")
    recs = ingestion_store.snapshot_records()
    cmp = period_comparison(recs, 1)
    # latest day 2026-07-01 (45.00) vs 2026-06-30 (40.00)
    assert cmp["current_usd"] == pytest.approx(45.0)
    assert cmp["previous_usd"] == pytest.approx(40.0)
    assert cmp["change_pct"] == pytest.approx(12.5)


def test_empty_dataset_is_safe():
    assert spend_trend([], "daily") == []
    assert spend_breakdown([], "service")["total_cost_usd"] == 0
    assert spend_overview([])["total_cost_usd"] == 0
    assert period_comparison([], 7)["change_pct"] is None


def test_invalid_dimension_and_granularity_rejected(small_records):
    with pytest.raises(ValueError):
        spend_breakdown(small_records, "colour")
    with pytest.raises(ValueError):
        spend_trend(small_records, "hourly")  # type: ignore[arg-type]


def test_benchmark_daily_totals_match_pure_python_recomputation():
    """Independent recomputation with csv + datetime only (no pandas, no engine code)."""
    ingest_file(FIXTURES / "billing_benchmark_v1.csv")
    engine = {s["period"]: s["cost_usd"] for s in spend_trend(ingestion_store.snapshot_records(), "daily")}

    oracle: dict[str, float] = defaultdict(float)
    with (FIXTURES / "billing_benchmark_v1.csv").open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            ts = datetime.fromisoformat(row["ChargePeriodStart"].replace("Z", "+00:00")).astimezone(timezone.utc)
            oracle[ts.strftime("%Y-%m-%d")] += float(row["BilledCost"])
    assert set(engine) == set(oracle)
    for day, value in oracle.items():
        assert engine[day] == pytest.approx(round(value, 2), abs=0.011)


def test_shares_use_gross_spend_when_credits_make_net_small():
    # Regression (public FOCUS sample): credits made the net total tiny and the
    # "unattributed share" came out negative. Shares now use absolute spend.
    from tests.test_ingestion import _row

    ingestion_store.ingest_rows(
        [
            _row(BilledCost="100", ResourceId="r-1"),
            _row(BilledCost="50", ResourceId=None, ServiceName="S3"),
            _row(BilledCost="-120", ChargeCategory="Credit", ResourceId=None, ServiceName="Credits"),
        ],
        raw_bytes=b"gross-share",
        source_name="t",
        source_type="t",
        schema_version="1.0",
    )
    result = spend_breakdown(ingestion_store.snapshot_records(), "resource")
    assert result["total_cost_usd"] == pytest.approx(30.0)  # net is still reported
    assert result["unattributed_share"] == pytest.approx(170 / 270, abs=1e-4)
    assert all(0 <= i["share"] <= 1 for i in result["items"])


def test_period_comparison_shrinks_window_to_available_data():
    # Regression: 91 days of data compared as "last 60 vs previous 60" showed +98% because the
    # previous window was half empty.
    from datetime import datetime, timedelta, timezone

    from finops_engine.analytics import period_comparison
    from finops_engine.schema import CloudProvider, FocusRecord

    t = datetime(2026, 4, 1, tzinfo=timezone.utc)
    recs = [
        FocusRecord(
            provider_name=CloudProvider.AWS,
            billed_cost=10.0,
            effective_cost=10.0,
            service_name="EC2",
            billing_period_start=t + timedelta(days=i),
            billing_period_end=t + timedelta(days=i + 1),
        )
        for i in range(91)
    ]
    c = period_comparison(recs, 60)
    assert c["window_days"] == 45 and c["window_shortened_to_fit_data"] is True
    assert c["change_pct"] == 0.0
    assert period_comparison(recs, 30)["window_shortened_to_fit_data"] is False
