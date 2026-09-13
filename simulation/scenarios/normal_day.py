""""Normal day" scenario: a household-shaped load, rooftop PV, a battery, and
a backup generator, driven through a scripted 24-hour day.

The control logic here is a fixed, explicit self-consumption script — battery
charges from excess PV and discharges to cover shortfalls, with the generator
as a last resort — not the real dispatch engine (that's M7). It exists to
exercise the M2 adapters end-to-end and produce plausible, inspectable
output.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from microgridmanager.adapters.simulated import (
    SimulatedBatteryAdapter,
    SimulatedGeneratorAdapter,
    SimulatedLoadAdapter,
    SimulatedPVAdapter,
    SimulationClock,
)
from microgridmanager.adapters.simulated.profiles import (
    daily_load_profile,
    daylight_irradiance_profile,
)

DEFAULT_START = datetime(2024, 6, 21, tzinfo=timezone.utc)
DEFAULT_STEP_SECONDS = 300.0
DEFAULT_DURATION_HOURS = 24.0


@dataclass(frozen=True)
class ScenarioAssets:
    clock: SimulationClock
    pv: SimulatedPVAdapter
    battery: SimulatedBatteryAdapter
    load: SimulatedLoadAdapter
    generator: SimulatedGeneratorAdapter


def build_scenario(
    *, start: datetime | None = None, step_seconds: float = DEFAULT_STEP_SECONDS
) -> ScenarioAssets:
    clock = SimulationClock(start=start or DEFAULT_START, step=timedelta(seconds=step_seconds))

    pv = SimulatedPVAdapter(
        "pv-1",
        clock=clock,
        rated_power_w=6_000.0,
        irradiance_profile=daylight_irradiance_profile(sunrise_hour=6.0, sunset_hour=19.0),
    )
    battery = SimulatedBatteryAdapter(
        "battery-1",
        clock=clock,
        capacity_wh=13_500.0,
        rated_power_w=5_000.0,
        initial_soc=0.4,
    )
    load = SimulatedLoadAdapter(
        "load-1",
        clock=clock,
        profile=daily_load_profile(
            base_watts=400.0, morning_peak_watts=1_800.0, evening_peak_watts=2_600.0
        ),
    )
    generator = SimulatedGeneratorAdapter("generator-1", clock=clock, rated_power_w=4_000.0)

    return ScenarioAssets(clock=clock, pv=pv, battery=battery, load=load, generator=generator)


def step_scenario(assets: ScenarioAssets, dt_seconds: float) -> dict:
    """Advance one control step: read PV/load, run the scripted
    self-consumption + backup-generator rule, integrate battery/generator,
    and return a flat dict of this step's readings."""
    pv_state = assets.pv.get_state()
    load_state = assets.load.get_state()

    net_surplus_w = pv_state.active_power_w - load_state.active_power_w
    assets.battery.set_active_power_w(-net_surplus_w)
    assets.battery.step(dt_seconds)
    battery_state = assets.battery.get_state()

    shortfall_w = load_state.active_power_w - pv_state.active_power_w - battery_state.active_power_w
    # Ignore sub-watt shortfalls: floating-point noise from the battery's own
    # rounding shouldn't spin up the generator for a fraction of a watt.
    assets.generator.set_active_power_w(shortfall_w if shortfall_w > 1.0 else 0.0)
    assets.generator.step(dt_seconds)
    generator_state = assets.generator.get_state()

    return {
        "timestamp": assets.clock.now.isoformat(),
        "pv_power_w": pv_state.active_power_w,
        "load_power_w": load_state.active_power_w,
        "battery_power_w": battery_state.active_power_w,
        "battery_soc": battery_state.state_of_charge,
        "generator_power_w": generator_state.active_power_w,
        "generator_fuel_consumed_l": assets.generator.fuel_consumed_l,
    }


def run(
    *,
    step_seconds: float = DEFAULT_STEP_SECONDS,
    duration_hours: float = DEFAULT_DURATION_HOURS,
    start: datetime | None = None,
) -> list[dict]:
    assets = build_scenario(start=start, step_seconds=step_seconds)
    num_steps = int(duration_hours * 3600.0 / step_seconds)

    rows = []
    for _ in range(num_steps):
        rows.append(step_scenario(assets, step_seconds))
        assets.clock.tick()

    return rows
