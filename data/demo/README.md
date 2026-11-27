# Sample datasets (generated, instructor-approved)

These four FOCUS-style daily bills were **generated** by the project team with pandas scripts.
The course instructor approved them as the reference data for this college project, because
no real cloud bills are available. They are not real cloud bills.

| File | Period | What it contains |
|---|---|---|
| `focus_compliant_demo_q2.csv` | 1 Apr – 30 Jun 2026 | **Q2 reference.** 12 resources on AWS, Azure and GCP; CPU 50–80% on weekdays, 10–40% at weekends |
| `focus_bill_jan_mar.csv` | 1 Jan – 31 Mar 2026 | Planted: AWS serverless 15–25× usage spike, 20–25 Feb |
| `focus_bill_mar_may.csv` | 1 Mar – 31 May 2026 | Planted: storage that grows every day (never cleaned up) |
| `focus_bill_may_june.csv` | 1 May – 30 Jun 2026 | Planted: GCP compute doubles overnight on 10 June |

**Bills for your own dates:** double-click `make_bill.cmd`, or run
`python -m scripts.generate_sample_bills --period jan-mar --currency INR` (also `--from 2026-04-01
--to 2026-09-30`). These come from one stable simulated estate (17 resources on AWS, Azure and
GCP, with accounts, regions, team and Kubernetes tags), so several bills can be uploaded together.
Each bill has a `.labels.json` file listing its planted anomalies.

**Regenerate them:** `generate_bills.cmd` (or `python -m scripts.generate_sample_bills`). It is
the team's four generator scripts (gen.py, jan-mar.py, marc-may.py, may-jun.py) merged into
one project tool and reproduces these files exactly. `--currency INR` writes the same bills priced
in rupees to `data/demo-inr/`; `--seed N` makes a new random variation in `output/bills/`.

The bills are in USD; the project reports them in rupees at ₹94.95 per $1 (set in
`config/finops-engine.sample.json`).

Run everything and get the output report: double-click `run_sample_report.cmd`
(writes `output\sample-report\report.html`). Show one dataset in the dashboard:
double-click `run_demo.cmd` (Q2 file) or drag another CSV onto it.
