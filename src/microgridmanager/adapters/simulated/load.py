"""Simulated generic controllable load adapter: follows a time-driven demand
profile by default, but accepts a direct setpoint override via
`set_active_power_w` (e.g. for curtailment by a future dispatch engine)."""

from __future__ import annotations

from microgridmanager.adapters.interface import (
    AssetAdapter,
    AssetType,
    LoadState,
    PowerControllable,
)

from .clock import SimulationClock
from .profiles import LoadProfile, constant_load_profile


class SimulatedLoadAdapter(AssetAdapter, PowerControllable):
    def __init__(
        self,
        asset_id: str,
        *,
        clock: SimulationClock | None = None,
        profile: LoadProfile | None = None,
    ) -> None:
        self._asset_id = asset_id
        self._clock = clock or SimulationClock()
        self._profile = profile or constant_load_profile(1_000.0)
        self._override_w: float | None = None

    @property
    def asset_id(self) -> str:
        return self._asset_id

    @property
    def asset_type(self) -> AssetType:
        return AssetType.LOAD

    def set_active_power_w(self, watts: float) -> None:
        self._override_w = watts

    def clear_override(self) -> None:
        self._override_w = None

    def get_state(self) -> LoadState:
        power = self._override_w if self._override_w is not None else self._profile(self._clock.now)
        return LoadState(
            asset_id=self._asset_id,
            asset_type=self.asset_type,
            timestamp=self._clock.now,
            online=True,
            active_power_w=power,
        )
