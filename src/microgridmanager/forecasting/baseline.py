"""M6 baseline `Forecaster` implementations.

`PersistenceForecaster` is the M4 dashboard's original inline forecast
(predict the next value as whatever was last observed), now behind the real
`Forecaster` interface instead of a one-off dashboard helper.

`SeasonalAverageForecaster` is the dev plan's "same-day-last-week average"
option, generalized to any cycle length via `period` (24h => "same time of
day, averaged over recent days"; 7 days => "same day, averaged over recent
weeks"). It's a meaningfully better baseline for the household scenario's
daily-repeating load/PV/EV patterns than raw persistence, while degrading to
a plain persistence forecast during the first cycle, before any seasonal
history exists yet — so a live dashboard session's first day isn't stuck
returning `fallback` the whole time.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

from .interface import Forecaster, HistoricalPoint

DEFAULT_PERIOD = timedelta(hours=24)
DEFAULT_MAX_LOOKBACK_CYCLES = 7
# How close to an anchor time (target_time minus N periods) a historical
# point must fall to count as "the same slot" — wide enough to tolerate a
# scenario's step size not evenly dividing the period, tight enough not to
# blur distinct times of day together.
DEFAULT_TOLERANCE_FRACTION = 0.02


class PersistenceForecaster(Forecaster):
    """Predicts `target_time`'s value as the most recently observed value
    before it, or `fallback` if there's no history yet."""

    def predict(
        self,
        history: Sequence[HistoricalPoint],
        target_time: datetime,
        *,
        fallback: float = 0.0,
    ) -> float:
        prior = _most_recent_before(history, target_time)
        return prior.value if prior is not None else fallback


class SeasonalAverageForecaster(Forecaster):
    """Predicts `target_time`'s value as the average of the value observed at
    the same point in each of the last `max_lookback_cycles` cycles of
    `period` before it. Falls back to a plain persistence forecast when no
    prior cycle has a matching point yet."""

    def __init__(
        self,
        *,
        period: timedelta = DEFAULT_PERIOD,
        max_lookback_cycles: int = DEFAULT_MAX_LOOKBACK_CYCLES,
        tolerance: timedelta | None = None,
    ) -> None:
        if period <= timedelta(0):
            raise ValueError("period must be positive")
        if max_lookback_cycles < 1:
            raise ValueError("max_lookback_cycles must be at least 1")
        self._period = period
        self._max_lookback_cycles = max_lookback_cycles
        self._tolerance = (
            tolerance if tolerance is not None else period * DEFAULT_TOLERANCE_FRACTION
        )

    def predict(
        self,
        history: Sequence[HistoricalPoint],
        target_time: datetime,
        *,
        fallback: float = 0.0,
    ) -> float:
        seasonal_values = []
        for cycle in range(1, self._max_lookback_cycles + 1):
            anchor = target_time - self._period * cycle
            match = _nearest_within(history, anchor, self._tolerance)
            if match is not None:
                seasonal_values.append(match.value)

        if seasonal_values:
            return sum(seasonal_values) / len(seasonal_values)

        prior = _most_recent_before(history, target_time)
        return prior.value if prior is not None else fallback


def _most_recent_before(
    history: Sequence[HistoricalPoint], target_time: datetime
) -> HistoricalPoint | None:
    prior = [point for point in history if point.timestamp < target_time]
    if not prior:
        return None
    return max(prior, key=lambda point: point.timestamp)


def _nearest_within(
    history: Sequence[HistoricalPoint], anchor: datetime, tolerance: timedelta
) -> HistoricalPoint | None:
    candidates = [point for point in history if abs(point.timestamp - anchor) <= tolerance]
    if not candidates:
        return None
    return min(candidates, key=lambda point: abs(point.timestamp - anchor))
