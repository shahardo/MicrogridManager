"""Simulated PV (solar) adapter: output driven purely by an irradiance
profile, not user-settable — matching a real PV inverter with no curtailment
control (curtailment can be added later as a `PowerControllable` mixin
without touching callers, per the M1 interface)."""

from __future__ import annotations

from microgridmanager.adapters.interface import AssetAdapter, AssetType, InverterState

from .clock import SimulationClock
from .profiles import IrradianceProfile, daylight_irradiance_profile


class SimulatedPVAdapter(AssetAdapter):
    def __init__(
        self,
        asset_id: str,
        *,
        clock: SimulationClock | None = None,
        rated_power_w: float = 5_000.0,
        irradiance_profile: IrradianceProfile | None = None,
    ) -> None:
        self._asset_id = asset_id
        self._clock = clock or SimulationClock()
        self._rated_power_w = rated_power_w
        self._irradiance_profile = irradiance_profile or daylight_irradiance_profile()

    @property
    def asset_id(self) -> str:
        return self._asset_id

    @property
    def asset_type(self) -> AssetType:
        return AssetType.INVERTER

    def get_state(self) -> InverterState:
        irradiance = max(0.0, min(1.0, self._irradiance_profile(self._clock.now)))
        return InverterState(
            asset_id=self._asset_id,
            asset_type=self.asset_type,
            timestamp=self._clock.now,
            online=True,
            active_power_w=self._rated_power_w * irradiance,
        )
