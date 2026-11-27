"""Generate sample bills (GENERATED data, not real cloud bills).

Approved sample bills (the team's four scripts gen.py, jan-mar.py, marc-may.py, may-jun.py in one
tool; the defaults reproduce data/demo/ exactly):
    python -m scripts.generate_sample_bills                   # USD, into data/demo/
    python -m scripts.generate_sample_bills --currency INR    # priced in rupees, into data/demo-inr/
    python -m scripts.generate_sample_bills --seed 7          # a new random variation, into output/bills/

Bills for your own dates (see scripts/period_bills.py), into output/bills/ with a .labels.json:
    python -m scripts.generate_sample_bills --period jan-mar --currency INR
    python -m scripts.generate_sample_bills --from 2026-04-01 --to 2026-09-30 --events none
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from scripts.period_bills import PRESETS, generate_period, parse_period

ROOT = Path(__file__).resolve().parents[1]

Row = dict[str, Any]


def _row(date_str: str, **fields: Any) -> Row:
    return {"ChargePeriodStart": f"{date_str}T00:00:00Z", "ChargePeriodEnd": f"{date_str}T23:59:59Z", **fields}


# q2: from gen.py

Q2_SERVICES = [
    ("AWS", "AmazonEC2", 0.10, "Hours", "Compute-t3.medium"),
    ("AWS", "AmazonS3", 0.023, "GB-Month", "Storage-Standard"),
    ("AWS", "AmazonRDS", 0.20, "Hours", "DB-db.m5.large"),
    ("AWS", "AWSLambda", 0.00001667, "GB-Seconds", "Serverless-Execution"),
    ("Azure", "Virtual Machines", 0.096, "Hours", "Compute-D2s_v3"),
    ("Azure", "Blob Storage", 0.018, "GB-Month", "Storage-HotLRS"),
    ("Azure", "Azure SQL", 0.25, "Hours", "DB-Gen5-2vCore"),
    ("Azure", "Azure Functions", 0.000016, "GB-Seconds", "Serverless-Execution"),
    ("GCP", "Compute Engine", 0.0475, "Hours", "Compute-e2-medium"),
    ("GCP", "Cloud Storage", 0.020, "GB-Month", "Storage-Standard"),
    ("GCP", "Cloud SQL", 0.15, "Hours", "DB-Custom-2-3840"),
    ("GCP", "Cloud Functions", 0.0000025, "Invocations", "Serverless-Execution"),
]


def scenario_q2(fx: float, currency: str, seed: int | None = None) -> list[Row]:
    rows: list[Row] = []
    np.random.seed(42 if seed is None else seed)
    for d in pd.date_range(start="2026-04-01", end="2026-06-30"):
        date_str = d.strftime("%Y-%m-%d")
        for prov, svc, rate, unit, utype in Q2_SERVICES:
            if "Compute" in utype or "Hours" in unit:
                qty = 24.0
            elif "Storage" in utype or "GB-Month" in unit:
                qty = 500.0 / 30.0
            elif "Lambda" in svc or "Functions" in svc:
                qty = float(np.random.randint(1000000, 5000000))
            else:
                qty = 24.0
            base_util = 25.0 if d.weekday() >= 5 else 65.0  # weekend dip
            util = max(5.0, min(100.0, base_util + np.random.uniform(-15.0, 15.0)))
            rows.append(
                _row(
                    date_str,
                    ProviderName=prov,
                    ServiceName=svc,
                    ResourceId=f"res-{prov.lower()}-{svc.lower().replace(' ', '-')}-001",
                    UsageType=utype,
                    UsageQuantity=round(qty, 4),
                    UsageUnit=unit,
                    ContractedUnitPrice=round(rate * fx, 8),
                    BilledCost=round(qty * rate * fx, 4),
                    BillingCurrency=currency,
                    ResourceUtilizationPercent=round(util, 2),
                )
            )
    return rows


# jan-mar: from jan-mar.py


def scenario_jan_mar(fx: float, currency: str, seed: int | None = None) -> list[Row]:
    rows: list[Row] = []
    np.random.seed(11 if seed is None else seed)
    for d in pd.date_range(start="2026-01-01", end="2026-03-31"):
        date_str = d.strftime("%Y-%m-%d")
        spike_window = "2026-02-20" <= date_str <= "2026-02-25"
        for prov in ("AWS", "Azure", "GCP"):
            for svc in ("Compute", "Storage", "Database", "Serverless"):
                base_qty = 24.0 if svc in ("Compute", "Database") else 800.0 / 30.0
                if spike_window and svc == "Serverless" and prov == "AWS":
                    qty = base_qty * np.random.uniform(15.0, 25.0)  # planted execution spike
                    util = np.random.uniform(95.0, 100.0)
                elif spike_window and svc == "Compute":
                    qty = base_qty
                    util = np.random.uniform(90.0, 98.0)  # planted CPU surge
                else:
                    qty = base_qty + np.random.uniform(-1.0, 1.0)
                    util = np.random.uniform(40.0, 60.0)
                rate = {"Compute": 0.12, "Storage": 0.02, "Database": 0.22}.get(svc, 0.00002)
                rows.append(_simple_row(date_str, prov, svc, "prod-02", qty, rate, util, fx, currency))
    return rows


# mar-may: from marc-may.py


def scenario_mar_may(fx: float, currency: str, seed: int | None = None) -> list[Row]:
    rows: list[Row] = []
    np.random.seed(22 if seed is None else seed)
    for idx, d in enumerate(pd.date_range(start="2026-03-01", end="2026-05-31")):
        date_str = d.strftime("%Y-%m-%d")
        drift = 1.0 + idx * 0.015  # planted: storage grows every day
        for prov in ("AWS", "Azure", "GCP"):
            for svc in ("Compute", "Storage", "Database"):
                rate = {"Compute": 0.09, "Storage": 0.025}.get(svc, 0.18)
                if svc == "Storage":
                    qty = (300.0 + np.random.uniform(5.0, 15.0)) * drift
                    util = 100.0
                else:
                    qty = 24.0
                    util = np.random.uniform(30.0, 70.0)
                rows.append(_simple_row(date_str, prov, svc, "dev-05", qty, rate, util, fx, currency))
    return rows


# may-jun: from may-jun.py


def scenario_may_jun(fx: float, currency: str, seed: int | None = None) -> list[Row]:
    rows: list[Row] = []
    np.random.seed(33 if seed is None else seed)
    for d in pd.date_range(start="2026-05-01", end="2026-06-30"):
        date_str = d.strftime("%Y-%m-%d")
        step_up = date_str >= "2026-06-10"  # planted: GCP compute doubles overnight
        for prov in ("AWS", "Azure", "GCP"):
            for svc in ("Compute", "Storage", "Database"):
                rate = {"Compute": 0.15, "Storage": 0.03}.get(svc, 0.30)
                if svc == "Compute" and prov == "GCP":
                    qty = 48.0 if step_up else 24.0
                    util = np.random.uniform(75.0, 85.0) if step_up else np.random.uniform(45.0, 55.0)
                else:
                    qty = 24.0 if svc != "Storage" else 450.0 / 30.0
                    util = np.random.uniform(40.0, 60.0)
                rows.append(_simple_row(date_str, prov, svc, "cluster-09", qty, rate, util, fx, currency))
    return rows


def _simple_row(date_str: str, prov: str, svc: str, env: str, qty: float, rate: float, util: float, fx: float,
                currency: str) -> Row:  # fmt: skip
    return _row(
        date_str,
        ProviderName=prov,
        ServiceName=f"{prov}_{svc}",
        ResourceId=f"res-{prov.lower()}-{svc.lower()}-{env}",
        UsageQuantity=round(qty, 4),
        ContractedUnitPrice=round(rate * fx, 5 if fx == 1.0 else 8),
        BilledCost=round(qty * rate * fx, 4),
        BillingCurrency=currency,
        ResourceUtilizationPercent=round(util, 2),
    )


SCENARIOS: dict[str, tuple[str, Callable[[float, str, int | None], list[Row]]]] = {
    "q2": ("focus_compliant_demo_q2.csv", scenario_q2),
    "jan-mar": ("focus_bill_jan_mar.csv", scenario_jan_mar),
    "mar-may": ("focus_bill_mar_may.csv", scenario_mar_may),
    "may-jun": ("focus_bill_may_june.csv", scenario_may_jun),
}


def configured_rate() -> float:
    cfg = json.loads((ROOT / "config" / "finops-engine.json").read_text(encoding="utf-8"))
    return float(cfg["ingestion"]["fx_rates_to_base"]["USD"])


def generate(
    scenario: str, currency: str = "USD", rate: float | None = None, seed: int | None = None
) -> tuple[str, pd.DataFrame]:
    name, fn = SCENARIOS[scenario]
    fx = 1.0 if currency == "USD" else (rate if rate is not None else configured_rate())
    return name, pd.DataFrame(fn(fx, currency, seed))


def _main_period(args: argparse.Namespace) -> None:
    from datetime import date, timedelta

    if args.period:
        start, end = parse_period(args.period, args.year)
    elif args.date_from and args.date_to:
        start, end = date.fromisoformat(args.date_from), date.fromisoformat(args.date_to)
    else:
        raise SystemExit("give --period (e.g. jan-mar) or both --from and --to")
    yesterday = date.today() - timedelta(days=1)
    if end > yesterday and not args.allow_future:
        # A real bill never contains days that have not happened yet (ingestion would reject them).
        print(
            f"note: stopping at {yesterday.isoformat()} (today is {date.today().isoformat()}); "
            "use --allow-future to override"
        )
        end = yesterday
    if end < start:
        raise SystemExit("the whole period is in the future; pick earlier months or a different --year")
    fx = 1.0 if args.currency == "USD" else (args.rate if args.rate is not None else configured_rate())
    df, meta = generate_period(
        start,
        end,
        currency=args.currency,
        fx=fx,
        seed=2026 if args.seed is None else args.seed,
        events=PRESETS[args.events],
    )
    out = Path(args.out_dir) if args.out_dir else ROOT / "output" / "bills"
    out.mkdir(parents=True, exist_ok=True)
    suffix = "" if args.currency == "USD" else "_inr"
    path = out / f"focus_bill_{start.isoformat()}_{end.isoformat()}{suffix}.csv"
    df.to_csv(path, index=False, lineterminator="\n")
    labels = path.with_suffix(".labels.json")
    labels.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    days = (end - start).days + 1
    print(f"{days} days, {len(df)} rows, total {df['BilledCost'].sum():,.2f} {args.currency}  ->  {path}")
    print(f"planted anomalies in this bill: {len(meta['labels'])}  ->  {labels.name}")
    for item in meta["labels"]:
        print(
            f"  {item['date']}  {item['provider']} {item['service']}: "
            f"{item['kind']} x{item['multiplier']} ({item['note']})"
        )
    for item in meta["other_planted_events"]:
        print(f"  {item['start']}..{item['end'] or 'end'}  {item['resource_id']}: {item['kind']} ({item['note']})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scenario", choices=[*SCENARIOS, "all"], default="all")
    parser.add_argument("--currency", type=str.upper, choices=["USD", "INR"], default="USD")
    parser.add_argument("--rate", type=float, help="rupees per USD for --currency INR (default: from the config)")
    parser.add_argument("--seed", type=int, help="new random variation (same planted events); default: original seeds")
    parser.add_argument("--out-dir", help="default: data/demo (USD), data/demo-inr (INR), output/bills (--seed)")
    parser.add_argument("--period", help="own dates by month, e.g. jan-mar, apr, oct-dec, nov-feb")
    parser.add_argument("--year", type=int, default=2026, help="year for --period (default 2026)")
    parser.add_argument("--from", dest="date_from", help="own dates: first day, YYYY-MM-DD")
    parser.add_argument("--to", dest="date_to", help="own dates: last day, YYYY-MM-DD")
    parser.add_argument(
        "--events", type=str.lower, choices=list(PRESETS), default="demo", help="planted problems (own dates only)"
    )
    parser.add_argument(
        "--allow-future", action="store_true", help="keep days after yesterday (they are rejected on upload)"
    )
    args = parser.parse_args()

    if args.period or args.date_from or args.date_to:
        _main_period(args)
        return

    if args.out_dir:
        out = Path(args.out_dir)
    elif args.seed is not None:
        out = ROOT / "output" / "bills"  # never overwrite the approved reference data with a variant
    else:
        out = ROOT / "data" / ("demo" if args.currency == "USD" else "demo-inr")
    out.mkdir(parents=True, exist_ok=True)
    for key in SCENARIOS if args.scenario == "all" else [args.scenario]:
        name, df = generate(key, args.currency, args.rate, args.seed)
        path = out / name
        df.to_csv(path, index=False, lineterminator="\n")
        total = df["BilledCost"].sum()
        print(f"{key:<8} {len(df):>5} rows  total {total:,.2f} {args.currency}  ->  {path}")


if __name__ == "__main__":
    main()
