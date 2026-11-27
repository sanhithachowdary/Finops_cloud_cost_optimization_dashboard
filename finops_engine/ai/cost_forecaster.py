"""Daily spend forecast built from linear regression on historical telemetry.

Returns point predictions with t-based 95% prediction intervals that widen
with the forecast horizon.
"""

from datetime import timedelta
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.linear_model import LinearRegression

from finops_engine.schema.focus_spec import FocusRecord, normalize_to_focus_dataframe


class CostForecaster:
    """Predictive spend model using linear trend estimation and parametric confidence intervals."""

    METHOD = "linear_regression_with_t_prediction_interval"

    def __init__(self, forecast_days: int = 30, min_history_days: int = 3):
        self.forecast_days = forecast_days
        self.min_history_days = max(3, min_history_days)
        self.model = LinearRegression()

    def predict_future_cost(self, records: list[FocusRecord]) -> dict[str, Any]:
        """Projects future spend trend with widening 95% prediction intervals."""
        df = normalize_to_focus_dataframe(records)
        if df.empty:
            return self._empty_result(0, "no cost records available")

        df["date"] = pd.to_datetime(df["billing_period_start"], utc=True).dt.date
        daily_total = df.groupby("date")["billed_cost"].sum().reset_index()
        return self.predict_from_daily_totals(daily_total)

    def _empty_result(self, history_days: int, reason: str) -> dict[str, Any]:
        return {
            "forecast_days": self.forecast_days,
            "total_projected_spend_usd": 0.0,
            "average_daily_projected_usd": 0.0,
            "confidence_level": "95%",
            "forecast": [],
            "status": "insufficient_data",
            "status_reason": reason,
            "method": self.METHOD,
            "history_days": history_days,
        }

    def predict_from_daily_totals(self, daily_total: pd.DataFrame) -> dict[str, Any]:
        """Forecast from a frame with ``date`` and ``billed_cost`` (one row per day)."""
        daily_total = daily_total.sort_values("date").reset_index(drop=True)
        if len(daily_total) < self.min_history_days:
            return self._empty_result(
                len(daily_total),
                f"{len(daily_total)} days of history; at least {self.min_history_days} required",
            )

        daily_total["day_idx"] = np.arange(len(daily_total))
        X = daily_total[["day_idx"]].values
        y = daily_total["billed_cost"].values
        n = len(y)

        self.model.fit(X, y)
        y_pred_hist = self.model.predict(X)
        residuals = y - y_pred_hist

        # Residual variance with degrees of freedom penalty for the fitted slope.
        dof = n - 2
        mse = float(np.sum(residuals**2)) / dof if dof > 0 else 0.0

        last_day_idx = n - 1
        last_date = daily_total["date"].max()
        future_indices = np.arange(last_day_idx + 1, last_day_idx + 1 + self.forecast_days)

        x_mean = float(np.mean(X))
        x_var = float(np.sum((X - x_mean) ** 2))
        effective_dof = max(3, dof) if dof > 0 else 3
        t_crit = float(stats.t.ppf(0.975, df=effective_dof))

        predictions = self.model.predict(future_indices.reshape(-1, 1))

        forecast_list: list[dict[str, Any]] = []
        total_projected = 0.0

        for idx, (day_idx, pred) in enumerate(zip(future_indices, predictions, strict=False)):
            # Prediction interval for the mean response at a given horizon.
            leverage = 1.0 / n + ((day_idx - x_mean) ** 2) / x_var if x_var > 0 else 0.0
            margin = t_crit * np.sqrt(mse * (1.0 + leverage))

            future_date = (last_date + timedelta(days=idx + 1)).isoformat()
            pred_cost = max(0.0, round(float(pred), 2))
            upper_bound = round(pred_cost + margin, 2)
            lower_bound = max(0.0, round(pred_cost - margin, 2))

            total_projected += pred_cost
            forecast_list.append(
                {
                    "date": future_date,
                    "predicted_cost_usd": pred_cost,
                    "confidence_upper_usd": upper_bound,
                    "confidence_lower_usd": lower_bound,
                }
            )

        return {
            "forecast_days": self.forecast_days,
            "total_projected_spend_usd": round(total_projected, 2),
            "average_daily_projected_usd": round(total_projected / self.forecast_days, 2)
            if self.forecast_days > 0
            else 0.0,
            "confidence_level": "95%",
            "forecast": forecast_list,
            "status": "ok",
            "status_reason": None,
            "method": self.METHOD,
            "history_days": n,
            "trend_usd_per_day": round(float(self.model.coef_[0]), 4),
        }
