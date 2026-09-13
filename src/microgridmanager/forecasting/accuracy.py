"""Forecast error metrics (M6): MAE and MAPE over paired actual/forecast
series, used by `scripts/forecast_report.py` and the accuracy tests to make
a `Forecaster`'s quality visible and comparable across models.
"""

from __future__ import annotations

from collections.abc import Sequence


def mean_absolute_error(actual: Sequence[float], forecast: Sequence[float]) -> float:
    _check_same_length(actual, forecast)
    if not actual:
        raise ValueError("actual/forecast must be non-empty")
    return sum(abs(a - f) for a, f in zip(actual, forecast)) / len(actual)


def mean_absolute_percentage_error(actual: Sequence[float], forecast: Sequence[float]) -> float:
    """Returns a percentage (5.0 means 5%). Points where `actual` is exactly
    zero are excluded rather than raising or dividing by zero — a series like
    PV power is legitimately zero overnight, and that shouldn't make the
    whole metric unusable."""
    _check_same_length(actual, forecast)
    pairs = [(a, f) for a, f in zip(actual, forecast) if a != 0]
    if not pairs:
        raise ValueError("no non-zero actual values to compute a percentage error against")
    return sum(abs((a - f) / a) for a, f in pairs) / len(pairs) * 100.0


def _check_same_length(actual: Sequence[float], forecast: Sequence[float]) -> None:
    if len(actual) != len(forecast):
        raise ValueError(
            f"actual and forecast must be the same length (got {len(actual)} and {len(forecast)})"
        )
