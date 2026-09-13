"""Simulated water heater adapter (M4): a thermal-storage-style load.

A virtual tank holds thermal energy in `[0, capacity_wh]`. Each `step()`
depletes it against a hot-water draw profile (showers, etc.) and, whenever
the heating element is on, replenishes it at `rated_power_w`. The element
cycles on/off with hysteresis — turning on once the tank drops below
`low_fraction` and staying on until it reaches `high_fraction` — like a real
thermostatically-controlled element, rather than turning on/off exactly at
one threshold (which would chatter every step).

`set_shed(True)` forces the element off regardless of hysteresis — this
adapter has no `PowerControllable` setpoint to override (it manages its own
heating decision), so M5's protection layer sheds it this way instead, the
same as cutting power at the breaker. The tank keeps depleting from hot-water
draws either way; only reheating is disabled.
"""

from __future__ import annotations

from microgridmanager.adapters.interface import AssetAdapter, AssetType, WaterHeaterState

from .clock import SimulationClock
from .profiles import WaterDrawProfile, household_water_draw_profile


class SimulatedWaterHeaterAdapter(AssetAdapter):
    def __init__(
        self,
        asset_id: str,
        *,
        clock: SimulationClock | None = None,
        capacity_wh: float = 4_000.0,
        rated_power_w: float = 4_500.0,
        initial_tank_fraction: float = 1.0,
        low_fraction: float = 0.4,
        high_fraction: float = 0.95,
        draw_profile: WaterDrawProfile | None = None,
    ) -> None:
        if not 0.0 <= initial_tank_fraction <= 1.0:
            raise ValueError("initial_tank_fraction must be within [0.0, 1.0]")
        self._asset_id = asset_id
        self._clock = clock or SimulationClock()
        self._capacity_wh = capacity_wh
        self._rated_power_w = rated_power_w
        self._low_fraction = low_fraction
        self._high_fraction = high_fraction
        self._draw_profile = draw_profile or household_water_draw_profile()
        self._tank_energy_wh = capacity_wh * initial_tank_fraction
        self._heating = False
        self._shed = False

    @property
    def asset_id(self) -> str:
        return self._asset_id

    @property
    def asset_type(self) -> AssetType:
        return AssetType.WATER_HEATER

    @property
    def tank_energy_fraction(self) -> float:
        return self._tank_energy_wh / self._capacity_wh

    def get_state(self) -> WaterHeaterState:
        return WaterHeaterState(
            asset_id=self._asset_id,
            asset_type=self.asset_type,
            timestamp=self._clock.now,
            online=True,
            active_power_w=self._rated_power_w if self._heating else 0.0,
            tank_energy_fraction=self.tank_energy_fraction,
            heating=self._heating,
        )

    def set_shed(self, shed: bool) -> None:
        """M5's protection layer calls this before `step()` to force the
        element off (`shed=True`) regardless of hysteresis, or to release it
        back to normal hysteresis control (`shed=False`)."""
        self._shed = shed

    def step(self, dt_seconds: float) -> None:
        """Advance one tick: deplete the tank by this step's hot-water draw,
        update the hysteresis heating state (unless shed), then replenish it
        if heating."""
        dt_hours = dt_seconds / 3600.0
        draw_w = max(0.0, self._draw_profile(self._clock.now))
        self._tank_energy_wh -= draw_w * dt_hours

        if self._shed:
            self._heating = False
        elif self.tank_energy_fraction <= self._low_fraction:
            self._heating = True
        elif self.tank_energy_fraction >= self._high_fraction:
            self._heating = False

        if self._heating:
            self._tank_energy_wh += self._rated_power_w * dt_hours

        self._tank_energy_wh = max(0.0, min(self._capacity_wh, self._tank_energy_wh))
