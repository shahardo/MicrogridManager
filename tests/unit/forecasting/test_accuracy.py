"""M6 exit-criteria tests for the accuracy metrics: known-value checks for
MAE/MAPE, plus the dev plan's "forecast accuracy within a defined bound on
synthetic data with known patterns" check — a synthetic daily-cycle series
where `SeasonalAverageForecaster` should both stay under an absolute error
bound and clearly beat plain persistence.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from microgridmanager.forecasting import (
    HistoricalPoint,
    PersistenceForecaster,
    SeasonalAverageForecaster,
    mean_absolute_error,
    mean_absolute_percentage_error,
)

START = datetime(2024, 1, 1, tzinfo=timezone.utc)


def test_mean_absolute_error_known_values() -> None:
    assert mean_absolute_error([10.0, 20.0, 30.0], [12.0, 18.0, 33.0]) == pytest.approx(
        (2.0 + 2.0 + 3.0) / 3.0
    )


def test_mean_absolute_percentage_error_known_values() -> None:
    assert mean_absolute_percentage_error([100.0, 200.0], [110.0, 180.0]) == pytest.approx(
        ((10.0 / 100.0) + (20.0 / 200.0)) / 2.0 * 100.0
    )


def test_mean_absolute_percentage_error_skips_zero_actuals() -> None:
    # The 0.0 actual would divide by zero if not excluded; only the second
    # pair should count.
    assert mean_absolute_percentage_error([0.0, 50.0], [5.0, 55.0]) == pytest.approx(10.0)


def test_metrics_reject_mismatched_lengths() -> None:
    with pytest.raises(ValueError):
        mean_absolute_error([1.0, 2.0], [1.0])
    with pytest.raises(ValueError):
        mean_absolute_percentage_error([1.0, 2.0], [1.0])


def _value_at_hour(hour_of_day: float) -> float:
    """A household-shaped daily step pattern (base / morning peak / midday /
    evening peak / base), the same known shape every day — standing in for
    a real load/PV profile's daily repetition."""
    if hour_of_day < 6.0:
        return 100.0
    if hour_of_day < 9.0:
        return 300.0
    if hour_of_day < 17.0:
        return 150.0
    if hour_of_day < 21.0:
        return 400.0
    return 150.0


def _synthetic_daily_series(num_days: int, *, step: timedelta) -> list[HistoricalPoint]:
    points = []
    num_steps = int(num_days * timedelta(days=1) / step)
    for i in range(num_steps):
        timestamp = START + step * i
        hour_of_day = (timestamp - START).total_seconds() / 3600.0 % 24.0
        points.append(HistoricalPoint(timestamp=timestamp, value=_value_at_hour(hour_of_day)))
    return points


def test_seasonal_average_beats_persistence_on_synthetic_daily_pattern() -> None:
    step = timedelta(minutes=5)
    series = _synthetic_daily_series(num_days=8, step=step)

    seasonal = SeasonalAverageForecaster(period=timedelta(hours=24), max_lookback_cycles=7)
    persistence = PersistenceForecaster()

    actual = []
    seasonal_forecast = []
    persistence_forecast = []
    history: list[HistoricalPoint] = []
    for point in series:
        actual.append(point.value)
        seasonal_forecast.append(
            seasonal.predict(history, point.timestamp, fallback=point.value)
        )
        persistence_forecast.append(
            persistence.predict(history, point.timestamp, fallback=point.value)
        )
        history.append(point)

    seasonal_mae = mean_absolute_error(actual, seasonal_forecast)
    persistence_mae = mean_absolute_error(actual, persistence_forecast)

    # Persistence's error is concentrated at each day's four step
    # transitions (it predicts "whatever the last tick was" right through
    # them); the seasonal model already knows the same transition happened
    # at this time yesterday, so it should track the repeating shape far
    # more tightly once it has a full day of history.
    assert seasonal_mae < 1.0
    assert seasonal_mae < persistence_mae
