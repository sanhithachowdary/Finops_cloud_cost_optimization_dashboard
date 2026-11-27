# FinOps engine output on the sample data

Generated 2026-10-24 09:06 UTC · config `2026.10-v2-sample-inr`

> GENERATED sample data, approved by the course instructor as the reference data for this project. Not a real cloud bill.
>
> All amounts in INR, converted at 1 USD = ₹94.95 (rate set in the config)

## KPI summary

| KPI | Measured | Target | Result | Basis |
|---|---|---|---|---|
| KPI-1 Estimated savings vs Q2 reference | 7.91% (₹29,211.91 of ₹3,69,263.97/month) | >= 5% | **MET** | focus_compliant_demo_q2.csv (Apr-Jun 2026); actionable recommendations only |
| KPI-2 Forecast accuracy | focus_bill_jan_mar.csv: 0.57%; focus_bill_mar_may.csv: 0.2%; focus_bill_may_june.csv: 0.86% (naive was better); focus_compliant_demo_q2.csv: 13.37% (naive was better) | Measure and report against approved reference data | **REPORTED** | 7-day hold-out back-test (MAPE) per file |
| KPI-3 Anomaly precision | precision 1.0 (1/1 alerts correct); recall 1/2 planted spike/step events | Compute against independent ground truth | **REPORTED** | Planted scenarios from the generator scripts (see PLANTED in the script) |
| KPI-4 Recovery rate | 20/20 faults recovered | Measure recoveries after fault injection | **REPORTED** | docs/evidence/kpi-results.json fault campaign |
| KPI-5 Service error rate | 0.0 (0/135) vs reference 0.0 | <= 0.8 x reference | **MET** | Reference = original v1.3.0 code, same machine and workload |
| KPI-6 p95 latency | 64.65 ms vs reference 1488.63 ms (ratio 0.043) | <= 0.80 x reference | **MET** | Reference = original v1.3.0 code, same machine and workload |

## focus_bill_jan_mar.csv

- Period 2026-01-01 to 2026-03-31; total ₹2,22,931.35; 1080 rows accepted, 0 rejected
- By provider: AWS ₹74,377.91, Azure ₹74,282.79, GCP ₹74,270.65
- Anomalies: 0
- Forecast next 30 days: ₹74,478.85
- Estimated savings: ₹14,915.99/month (20.05% of ₹74,401.68/month)

## focus_bill_mar_may.csv

- Period 2026-03-01 to 2026-05-31; total ₹5,11,591.56; 828 rows accepted, 0 rejected
- By provider: GCP ₹1,70,552.48, Azure ₹1,70,549.65, AWS ₹1,70,489.44
- Anomalies: 0
- Forecast next 30 days: ₹2,27,578.02
- Estimated savings: ₹12,256.29/month (6.2% of ₹1,97,686.36/month)

## focus_bill_may_june.csv

- Period 2026-05-01 to 2026-06-30; total ₹2,02,656.53; 549 rows accepted, 0 rejected
- By provider: GCP ₹72,337.66, AWS ₹65,159.44, Azure ₹65,159.44
- Anomalies: 1
  - 2026-06-10 GCP GCP_Compute: Daily spend ₹683.64 jumped off a flat 14-day baseline of ₹341.82/day (+100%)
- Forecast next 30 days: ₹1,10,030.22
- Estimated savings: ₹18,376.24/month (17.79% of ₹1,03,315.10/month)

## focus_compliant_demo_q2.csv

- Period 2026-04-01 to 2026-06-30; total ₹11,29,942.39; 1092 rows accepted, 0 rejected
- By provider: AWS ₹5,13,909.96, Azure ₹5,05,313.40, GCP ₹1,10,719.03
- Anomalies: 0
- Forecast next 30 days: ₹3,73,427.71
- Estimated savings: ₹29,211.91/month (7.91% of ₹3,69,263.97/month)
