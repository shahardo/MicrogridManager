"""Simulated battery energy storage (BESS) adapter.

Sign convention: positive `active_power_w` is discharging (power flowing out
to the site), negative is charging. `set_active_power_w` records the
requested setpoint immediately (so it round-trips through `get_state()`
without needing a `step()`, matching the shared adapter contract); `step()`
integrates state of charge over an elapsed interval, clamping the *applied*
power to the battery's rated power and to whatever energy is actually
available/chargeable — degradation modeling is deferred, per the Phase 1
plan.
"""

from __future__ import annotations

from microgridmanager.adapters.interface import (
    AssetAdapter,
    AssetType,
    BatteryState,
    PowerControllable,
    StateOfChargeReadable,
)

from .clock import SimulationClock


class SimulatedBatteryAdapter(AssetAdapter, PowerControllable, StateOfChargeReadable):
    def __init__(
        self,
        asset_id: str,
        *,
        clock: SimulationClock | None = None,
        capacity_wh: float = 10_000.0,
        rated_power_w: float = 5_000.0,
        initial_soc: float = 0.5,
        round_trip_efficiency: float = 0.95,
    ) -> None:
        if not 0.0 <= initial_soc <= 1.0:
            raise ValueError("initial_soc must be within [0.0, 1.0]")
        self._asset_id = asset_id
        self._clock = clock or SimulationClock()
        self._capacity_wh = capacity_wh
        self._rated_power_w = rated_power_w
        self._round_trip_efficiency = round_trip_efficiency
        self._energy_wh = capacity_wh * initial_soc
        self._active_power_w = 0.0

    @property
    def asset_id(self) -> str:
        return self._asset_id

    @property
    def asset_type(self) -> AssetType:
        return AssetType.BATTERY_STORAGE

    def set_active_power_w(self, watts: float) -> None:
        self._active_power_w = watts

    def get_state_of_charge(self) -> float:
        return self._energy_wh / self._capacity_wh

    def get_state(self) -> BatteryState:
        return BatteryState(
            asset_id=self._asset_id,
            asset_type=self.asset_type,
            timestamp=self._clock.now,
            online=True,
            active_power_w=self._active_power_w,
            state_of_charge=self.get_state_of_charge(),
        )

    def step(self, dt_seconds: float) -> None:
        """Integrate state of charge over `dt_seconds` at the current
        setpoint, clamping the applied power to rated power and to available
        headroom/reserves so state of charge never leaves [0.0, 1.0]."""
        requested_w = max(-self._rated_power_w, min(self._rated_power_w, self._active_power_w))
        dt_hours = dt_seconds / 3600.0

        if dt_hours <= 0.0 or requested_w == 0.0:
            self._active_power_w = 0.0
            return

        if requested_w < 0.0:  # charging
            max_chargeable_wh = self._capacity_wh - self._energy_wh
            energy_in_wh = -requested_w * dt_hours * self._round_trip_efficiency
            energy_in_wh = max(0.0, min(energy_in_wh, max_chargeable_wh))
            self._energy_wh += energy_in_wh
            self._active_power_w = -(energy_in_wh / (dt_hours * self._round_trip_efficiency))
        else:  # discharging
            max_dischargeable_wh = self._energy_wh
            energy_out_wh = min(requested_w * dt_hours, max_dischargeable_wh)
            energy_out_wh = max(0.0, energy_out_wh)
            self._energy_wh -= energy_out_wh
            self._active_power_w = energy_out_wh / dt_hours

        self._energy_wh = max(0.0, min(self._capacity_wh, self._energy_wh))
