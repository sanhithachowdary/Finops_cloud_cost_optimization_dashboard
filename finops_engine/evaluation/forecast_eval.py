"""Forecast back-testing on held-out data (KPI-2).

The last ``holdout_days`` of daily total spend are hidden, the model is fitted
on the remaining history and the forecast is compared with the hidden actuals.
Two naive baselines are scored on exactly the same split for comparison:

* ``naive_last_value`` – repeat the last observed day
* ``naive_mean_7d``    – repeat the mean of the last 7 observed days

Metrics: MAE and RMSE (USD/day) and MAPE (%).  MAPE is reported only when every
held-out actual is strictly positive; otherwise it is ``None`` with a reason,
because MAPE is undefined/unstable near zero.
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd

from finops_engine.ai.cost_forecaster import CostForecaster
from finops_engine.schema.focus_spec import FocusRecord, normalize_to_focus_dataframe


def _metrics(actual: list[float], predicted: list[float]) -> dict[str, Any]:
    errors = [p - a for a, p in zip(actual, predicted, strict=True)]
    mae = sum(abs(e) for e in errors) / len(errors)
    rmse = math.sqrt(sum(e * e for e in errors) / len(errors))
    if all(a > 0 for a in actual):
        mape: float | None = 100.0 * sum(abs(e) / a for e, a in zip(errors, actual, strict=True)) / len(errors)
        mape_note = None
    else:
        mape, mape_note = None, "MAPE undefined: held-out actuals contain zero/negative values"
    return {
        "mae_usd": round(mae, 2),
        "rmse_usd": round(rmse, 2),
        "mape_pct": round(mape, 2) if mape is not None else None,
        "mape_note": mape_note,
    }


def daily_totals(records: list[FocusRecord]) -> pd.DataFrame:
    df = normalize_to_focus_dataframe(records)
    if df.empty:
        return pd.DataFrame(columns=["date", "billed_cost"])
    df["date"] = pd.to_datetime(df["billing_period_start"], utc=True).dt.date
    return df.groupby("date")["billed_cost"].sum().reset_index().sort_values("date").reset_index(drop=True)


def backtest_forecast(records: list[FocusRecord], holdout_days: int = 14, min_history_days: int = 14) -> dict[str, Any]:
    daily = daily_totals(records)
    if holdout_days < 1:
        raise ValueError("holdout_days must be >= 1")
    if len(daily) < holdout_days + min_history_days:
        return {
            "status": "insufficient_data",
            "status_reason": f"{len(daily)} days available; need {holdout_days + min_history_days} "
            f"({min_history_days} history + {holdout_days} holdout)",
        }
    train = daily.iloc[:-holdout_days]
    test = daily.iloc[-holdout_days:]
    actual = [float(v) for v in test["billed_cost"]]

    forecaster = CostForecaster(forecast_days=holdout_days, min_history_days=min_history_days)
    result = forecaster.predict_from_daily_totals(train)
    predicted = [float(item["predicted_cost_usd"]) for item in result["forecast"]]
    inside = sum(
        1
        for a, item in zip(actual, result["forecast"], strict=True)
        if item["confidence_lower_usd"] <= a <= item["confidence_upper_usd"]
    )

    history = [float(v) for v in train["billed_cost"]]
    last_value = [history[-1]] * holdout_days
    mean_7 = [sum(history[-7:]) / len(history[-7:])] * holdout_days

    model_metrics = _metrics(actual, predicted)
    baselines = {
        "naive_last_value": _metrics(actual, last_value),
        "naive_mean_7d": _metrics(actual, mean_7),
    }
    best_baseline_mae = min(b["mae_usd"] for b in baselines.values())
    return {
        "status": "ok",
        "method": result["method"],
        "train_days": len(train),
        "holdout_days": holdout_days,
        "train_period": [str(train["date"].iloc[0]), str(train["date"].iloc[-1])],
        "holdout_period": [str(test["date"].iloc[0]), str(test["date"].iloc[-1])],
        "model": model_metrics,
        "baselines": baselines,
        "prediction_interval_coverage": round(inside / holdout_days, 4),
        "nominal_interval": 0.95,
        "beats_best_naive_baseline_on_mae": model_metrics["mae_usd"] < best_baseline_mae,
        "holdout_total_actual_usd": round(sum(actual), 2),
        "holdout_total_predicted_usd": round(sum(predicted), 2),
    }
