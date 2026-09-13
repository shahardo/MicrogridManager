"""Model-specific behavior for the M6 baseline forecasters, beyond what the
shared contract suite covers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from microgridmanager.forecasting import (
    HistoricalPoint,
    PersistenceForecaster,
    SeasonalAverageForecaster,
)

START = datetime(2024, 1, 1, tzinfo=timezone.utc)


def test_persistence_predicts_most_recent_observation() -> None:
    forecaster = PersistenceForecaster()
    history = [
        HistoricalPoint(timestamp=START - timedelta(hours=2), value=10.0),
        HistoricalPoint(timestamp=START - timedelta(hours=1), value=20.0),
    ]
    assert forecaster.predict(history, START, fallback=0.0) == 20.0


def test_persistence_ignores_out_of_order_history() -> None:
    forecaster = PersistenceForecaster()
    history = [
        HistoricalPoint(timestamp=START - timedelta(hours=1), value=20.0),
        HistoricalPoint(timestamp=START - timedelta(hours=5), value=10.0),
    ]
    assert forecaster.predict(history, START, fallback=0.0) == 20.0


def test_seasonal_average_falls_back_to_persistence_before_first_full_cycle() -> None:
    """During the first day of a live run there's no "same time yesterday"
    sample yet, so the model should degrade to a plain persistence forecast
    rather than just returning `fallback` for the whole first cycle."""
    forecaster = SeasonalAverageForecaster(period=timedelta(hours=24))
    history = [
        HistoricalPoint(timestamp=START - timedelta(hours=2), value=10.0),
        HistoricalPoint(timestamp=START - timedelta(hours=1), value=30.0),
    ]
    assert forecaster.predict(history, START, fallback=0.0) == 30.0


def test_seasonal_average_uses_same_time_of_day_once_available() -> None:
    forecaster = SeasonalAverageForecaster(period=timedelta(hours=24), max_lookback_cycles=7)
    history = [
        HistoricalPoint(timestamp=START - timedelta(hours=24), value=100.0),
        # A near, unrelated observation that a persistence model would use
        # instead — the seasonal model should prefer the same-time-of-day
        # sample over this one.
        HistoricalPoint(timestamp=START - timedelta(minutes=5), value=5.0),
    ]
    assert forecaster.predict(history, START, fallback=0.0) == 100.0


def test_seasonal_average_averages_across_multiple_cycles() -> None:
    forecaster = SeasonalAverageForecaster(period=timedelta(hours=24), max_lookback_cycles=3)
    history = [
        HistoricalPoint(timestamp=START - timedelta(hours=24), value=100.0),
        HistoricalPoint(timestamp=START - timedelta(hours=48), value=200.0),
        HistoricalPoint(timestamp=START - timedelta(hours=72), value=300.0),
    ]
    assert forecaster.predict(history, START, fallback=0.0) == 200.0


def test_seasonal_average_respects_max_lookback_cycles() -> None:
    forecaster = SeasonalAverageForecaster(period=timedelta(hours=24), max_lookback_cycles=2)
    history = [
        HistoricalPoint(timestamp=START - timedelta(hours=24), value=100.0),
        HistoricalPoint(timestamp=START - timedelta(hours=48), value=200.0),
        # Outside the 2-cycle lookback window, should be ignored.
        HistoricalPoint(timestamp=START - timedelta(hours=72), value=900.0),
    ]
    assert forecaster.predict(history, START, fallback=0.0) == 150.0


def test_seasonal_average_rejects_non_positive_period() -> None:
    with pytest.raises(ValueError):
        SeasonalAverageForecaster(period=timedelta(0))
