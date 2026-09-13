"""M6 forecasting interface: the load-bearing seam between historical series
and a point forecast, mirroring how `adapters/interface.py` seams real vs.
simulated assets behind one contract.

`Forecaster` knows nothing about which asset or series it's forecasting —
callers (the M4 dashboard's live engine, `scripts/forecast_report.py`, and
eventually M7's dispatch engine) hand it a plain list of past observations
and a time to predict, and get a number back. That's what lets M7 "consume
forecasts without knowing which model produced them" (this milestone's exit
criteria): swapping `PersistenceForecaster` for `SeasonalAverageForecaster`,
or a future ML-based model, never changes a caller.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class HistoricalPoint:
    """One observed (timestamp, value) sample a `Forecaster` can learn from."""

    timestamp: datetime
    value: float


class Forecaster(ABC):
    """A model that predicts a series' value at a given time from its past.

    Implementations must only use points strictly *before* `target_time` —
    `history` may contain points at or after it (a caller shouldn't have to
    pre-filter its own buffer), but using them would be forecasting a value
    from itself.
    """

    @abstractmethod
    def predict(
        self,
        history: Sequence[HistoricalPoint],
        target_time: datetime,
        *,
        fallback: float = 0.0,
    ) -> float:
        """Return a point forecast for `target_time`. `fallback` is returned
        verbatim when `history` has nothing usable to predict from yet (e.g.
        the very first tick of a run)."""
