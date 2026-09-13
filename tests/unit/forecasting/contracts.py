"""Shared contract assertions for `Forecaster` implementations.

Every forecaster — baseline (M6) or a future ML-based model — must satisfy
these. Kept as plain functions (like `tests/unit/adapters/contracts.py`) so
any test file can import and reuse them directly.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from microgridmanager.forecasting.interface import Forecaster, HistoricalPoint

START = datetime(2024, 1, 1, tzinfo=timezone.utc)


def assert_empty_history_returns_fallback(forecaster: Forecaster) -> None:
    assert forecaster.predict([], START, fallback=42.0) == 42.0


def assert_deterministic(forecaster: Forecaster) -> None:
    history = [
        HistoricalPoint(timestamp=START - timedelta(hours=1), value=10.0),
        HistoricalPoint(timestamp=START - timedelta(minutes=30), value=12.0),
    ]
    target_time = START
    first = forecaster.predict(history, target_time, fallback=0.0)
    second = forecaster.predict(history, target_time, fallback=0.0)
    assert first == second


def assert_constant_history_is_predicted_exactly(forecaster: Forecaster) -> None:
    """If every observation the forecaster can see is the same constant
    value, the forecast for any later time should be that same value —
    true of a plain persistence forecast and of an average over identical
    seasonal samples alike."""
    constant = 100.0
    history = [
        HistoricalPoint(timestamp=START - timedelta(hours=i), value=constant)
        for i in range(1, 25)
    ]
    forecast = forecaster.predict(history, START, fallback=0.0)
    assert forecast == constant


def assert_ignores_points_at_or_after_target(forecaster: Forecaster) -> None:
    """A forecaster must not use `target_time`'s own (or a later) value to
    predict itself — otherwise it isn't a real forecast."""
    history = [
        HistoricalPoint(timestamp=START - timedelta(hours=1), value=5.0),
        HistoricalPoint(timestamp=START, value=999.0),
        HistoricalPoint(timestamp=START + timedelta(hours=1), value=999.0),
    ]
    forecast = forecaster.predict(history, START, fallback=0.0)
    assert forecast != 999.0


def assert_forecast_contract(forecaster: Forecaster) -> None:
    assert_empty_history_returns_fallback(forecaster)
    assert_deterministic(forecaster)
    assert_constant_history_is_predicted_exactly(forecaster)
    assert_ignores_points_at_or_after_target(forecaster)
