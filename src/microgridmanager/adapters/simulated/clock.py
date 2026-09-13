"""Discrete time-step simulation clock shared by all simulated adapters.

Every simulated adapter reads the same clock's `now` for state timestamps and
for evaluating time-driven profiles (irradiance, load), so a single `tick()`
advances the whole simulated site consistently.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

_DEFAULT_START = datetime(2024, 1, 1, tzinfo=timezone.utc)
_DEFAULT_STEP = timedelta(minutes=1)


@dataclass
class SimulationClock:
    start: datetime = field(default_factory=lambda: _DEFAULT_START)
    step: timedelta = field(default_factory=lambda: _DEFAULT_STEP)
    _elapsed_steps: int = field(default=0, init=False, repr=False)

    @property
    def now(self) -> datetime:
        return self.start + self._elapsed_steps * self.step

    @property
    def elapsed_seconds(self) -> float:
        return self._elapsed_steps * self.step.total_seconds()

    def tick(self) -> None:
        self._elapsed_steps += 1

    def reset(self) -> None:
        self._elapsed_steps = 0
