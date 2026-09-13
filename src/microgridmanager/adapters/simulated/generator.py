"""Simulated diesel/gas generator adapter: a fuel-curve driven power source.

Unlike the battery/load adapters, requested setpoints are clamped to
[0, rated_power_w] immediately in `set_active_power_w` — a genset can be
throttled but can neither run in reverse nor exceed nameplate rating, so
this is enforced at the point of control rather than deferred to a `step()`.
Fuel consumption is tracked internally (via `step()`/`fuel_consumed_l`) for
inspection and future dispatch cost accounting; it is not part of the M1
`GeneratorState` contract.
"""

from __future__ import annotations

from microgridmanager.adapters.interface import (
    AssetAdapter,
    AssetType,
    GeneratorState,
    PowerControllable,
)

from .clock import SimulationClock
from .profiles import FuelCurve, linear_fuel_curve


class SimulatedGeneratorAdapter(AssetAdapter, PowerControllable):
    def __init__(
        self,
        asset_id: str,
        *,
        clock: SimulationClock | None = None,
        rated_power_w: float = 5_000.0,
        fuel_curve: FuelCurve | None = None,
    ) -> None:
        self._asset_id = asset_id
        self._clock = clock or SimulationClock()
        self._rated_power_w = rated_power_w
        self._fuel_curve = fuel_curve or linear_fuel_curve()
        self._active_power_w = 0.0
        self._fuel_consumed_l = 0.0

    @property
    def asset_id(self) -> str:
        return self._asset_id

    @property
    def asset_type(self) -> AssetType:
        return AssetType.GENERATOR

    def set_active_power_w(self, watts: float) -> None:
        self._active_power_w = max(0.0, min(self._rated_power_w, watts))

    def get_state(self) -> GeneratorState:
        return GeneratorState(
            asset_id=self._asset_id,
            asset_type=self.asset_type,
            timestamp=self._clock.now,
            online=True,
            active_power_w=self._active_power_w,
        )

    def step(self, dt_seconds: float) -> None:
        fuel_rate_lph = self._fuel_curve(self._active_power_w, self._rated_power_w)
        self._fuel_consumed_l += fuel_rate_lph * (dt_seconds / 3600.0)

    @property
    def fuel_consumed_l(self) -> float:
        return self._fuel_consumed_l
