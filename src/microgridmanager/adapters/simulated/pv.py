"""Simulated PV (solar) adapter: output driven purely by an irradiance
profile, not user-settable — matching a real PV inverter with no curtailment
control (curtailment can be added later as a `PowerControllable` mixin
without touching callers, per the M1 interface).

`set_output_multiplier` (added for the dashboard's "clouds" disturbance
control) is deliberately *not* that curtailment mixin: it scales the
irradiance-driven output continuously (e.g. 0.3 to simulate heavy cloud
cover), it isn't a `PowerControllable` absolute setpoint a dispatcher would
write to, and unlike curtailment it can also raise output back above the
irradiance profile's own curve (multiplier > 1) — clamped at
`[0.0, rated_power_w]` in `get_state()` either way since a PV inverter can
never exceed nameplate or go negative."""

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
        self._output_multiplier = 1.0

    @property
    def asset_id(self) -> str:
        return self._asset_id

    @property
    def asset_type(self) -> AssetType:
        return AssetType.INVERTER

    def set_output_multiplier(self, multiplier: float) -> None:
        """Scale output relative to the irradiance profile's own curve (1.0 =
        no effect). Used by the dashboard's "clouds" disturbance control to
        depress PV output without touching the underlying irradiance
        profile, so clearing the disturbance (multiplier back to 1.0)
        exactly restores the profile-driven curve."""
        if multiplier < 0.0:
            raise ValueError("multiplier must be non-negative")
        self._output_multiplier = multiplier

    def get_state(self) -> InverterState:
        irradiance = max(0.0, min(1.0, self._irradiance_profile(self._clock.now)))
        active_power_w = self._rated_power_w * irradiance * self._output_multiplier
        active_power_w = max(0.0, min(self._rated_power_w, active_power_w))
        return InverterState(
            asset_id=self._asset_id,
            asset_type=self.asset_type,
            timestamp=self._clock.now,
            online=True,
            active_power_w=active_power_w,
        )
