"""The forecaster contract test suite (M6 visible result, mirroring M1's
adapter contract suite): every `Forecaster` implementation is checked against
the same shared contract in `contracts.py`. Future ML-based models should
extend this parametrization rather than get their own separate checks.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from microgridmanager.forecasting import PersistenceForecaster, SeasonalAverageForecaster

from .contracts import assert_forecast_contract

FORECASTERS = [
    PersistenceForecaster(),
    SeasonalAverageForecaster(period=timedelta(hours=24), max_lookback_cycles=7),
    SeasonalAverageForecaster(period=timedelta(days=7), max_lookback_cycles=4),
]


@pytest.mark.parametrize("forecaster", FORECASTERS, ids=lambda f: type(f).__name__)
def test_forecast_contract(forecaster) -> None:
    assert_forecast_contract(forecaster)
