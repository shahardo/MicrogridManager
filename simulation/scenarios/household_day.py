""""Household day" scenario (M4): the extended six-device simulated
environment — rooftop PV, a battery, a household load, an EV charger, a
water heater, and a grid connection — driven through a scripted 24-hour day.

Like `normal_day`, the control logic here is a fixed, explicit
self-consumption script (battery charges from excess PV, discharges to cover
shortfalls) with the grid connection absorbing whatever residual import/
export is left over — not the real dispatch engine (M7). The EV charger and
water heater manage their own charging/reheat decisions internally (see their
adapters' docstrings); this scenario just steps them and reads their status.
It exists to exercise all six M4 adapters end-to-end and to give the M4
dashboard real, plausible live and replay data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from microgridmanager.adapters.simulated import (
    EVSession,
    SimulatedBatteryAdapter,
    SimulatedEVChargerAdapter,
    SimulatedGridConnectionAdapter,
    SimulatedLoadAdapter,
    SimulatedPVAdapter,
    SimulatedWaterHeaterAdapter,
    SimulationClock,
)
from microgridmanager.adapters.simulated.profiles import (
    daily_load_profile,
    daylight_irradiance_profile,
    household_water_draw_profile,
    time_of_use_tariff_profile,
)

DEFAULT_START = datetime(2024, 6, 21, tzinfo=timezone.utc)
DEFAULT_STEP_SECONDS = 300.0
DEFAULT_DURATION_HOURS = 24.0
DEFAULT_EV_SESSION_HORIZON_DAYS = 365


def _daily_ev_sessions(
    start: datetime,
    *,
    num_days: int = DEFAULT_EV_SESSION_HORIZON_DAYS,
    evening_plug_in_hour: float = 18.0,
    morning_deadline_hour: float = 6.0,
    target_soc: float = 0.9,
) -> list[EVSession]:
    """A recurring daily commute pattern — plug in every evening, charge
    overnight, unplug for the morning commute — repeated for `num_days`.

    Without this, a scenario built with a fixed, one-off list of sessions
    (the original M4 design) goes permanently flat — unplugged, 0 W, constant
    state of charge — the moment the clock passes its last session. That's
    invisible in a bounded 24h batch run but very visible as a flat EV line
    the moment a live dashboard session runs past its first simulated day.
    The first session starts at `start` itself (a charge already under way
    from the previous evening), matching the original scenario's opening
    behaviour.
    """
    day_start = start.replace(hour=0, minute=0, second=0, microsecond=0)
    first_deadline = day_start + timedelta(hours=morning_deadline_hour)
    if first_deadline <= start:
        first_deadline += timedelta(days=1)
    sessions = [EVSession(plug_in=start, deadline=first_deadline, target_soc=0.8)]

    for day in range(num_days):
        plug_in = day_start + timedelta(days=day, hours=evening_plug_in_hour)
        deadline = day_start + timedelta(days=day + 1, hours=morning_deadline_hour)
        if plug_in < start:
            continue
        sessions.append(EVSession(plug_in=plug_in, deadline=deadline, target_soc=target_soc))

    return sessions


@dataclass(frozen=True)
class HouseholdScenarioAssets:
    clock: SimulationClock
    pv: SimulatedPVAdapter
    battery: SimulatedBatteryAdapter
    household_load: SimulatedLoadAdapter
    ev_charger: SimulatedEVChargerAdapter
    water_heater: SimulatedWaterHeaterAdapter
    grid: SimulatedGridConnectionAdapter


def build_scenario(
    *, start: datetime | None = None, step_seconds: float = DEFAULT_STEP_SECONDS
) -> HouseholdScenarioAssets:
    start = start or DEFAULT_START
    clock = SimulationClock(start=start, step=timedelta(seconds=step_seconds))

    pv = SimulatedPVAdapter(
        "pv-rooftop",
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
    household_load = SimulatedLoadAdapter(
        "household-load",
        clock=clock,
        profile=daily_load_profile(
            base_watts=400.0, morning_peak_watts=1_800.0, evening_peak_watts=2_600.0
        ),
    )
    ev_charger = SimulatedEVChargerAdapter(
        "ev-charger-1",
        clock=clock,
        capacity_wh=60_000.0,
        rated_power_w=7_200.0,
        initial_soc=0.3,
        sessions=_daily_ev_sessions(start),
        # A daily commute's worth of driving (~30 miles at ~300 Wh/mile) so
        # the car actually needs the next overnight session's charge instead
        # of arriving already at (or above) its target state of charge.
        commute_energy_wh=9_000.0,
    )
    water_heater = SimulatedWaterHeaterAdapter(
        "water-heater-1",
        clock=clock,
        capacity_wh=4_000.0,
        rated_power_w=4_500.0,
        initial_tank_fraction=1.0,
        draw_profile=household_water_draw_profile(),
    )
    grid = SimulatedGridConnectionAdapter(
        "grid-1", clock=clock, tariff_profile=time_of_use_tariff_profile()
    )

    return HouseholdScenarioAssets(
        clock=clock,
        pv=pv,
        battery=battery,
        household_load=household_load,
        ev_charger=ev_charger,
        water_heater=water_heater,
        grid=grid,
    )


def step_scenario(assets: HouseholdScenarioAssets, dt_seconds: float) -> dict:
    """Advance one control step: read PV/household load, step the EV charger
    and water heater (each manages its own charging/reheat decision), run the
    scripted battery self-consumption rule, and send whatever's left over
    through the grid connection. Returns a flat dict of this step's readings,
    the visible per-tick record every later milestone (and the M4 dashboard)
    reads."""
    pv_state = assets.pv.get_state()
    household_state = assets.household_load.get_state()

    assets.ev_charger.step(dt_seconds)
    ev_state = assets.ev_charger.get_state()

    assets.water_heater.step(dt_seconds)
    water_heater_state = assets.water_heater.get_state()

    total_load_w = (
        household_state.active_power_w + ev_state.active_power_w + water_heater_state.active_power_w
    )
    net_surplus_w = pv_state.active_power_w - total_load_w
    assets.battery.set_active_power_w(-net_surplus_w)
    assets.battery.step(dt_seconds)
    battery_state = assets.battery.get_state()

    residual_w = total_load_w - pv_state.active_power_w - battery_state.active_power_w
    assets.grid.set_net_power_w(residual_w)
    assets.grid.step(dt_seconds)
    grid_state = assets.grid.get_state()

    return {
        "timestamp": assets.clock.now.isoformat(),
        "pv_power_w": pv_state.active_power_w,
        "household_load_power_w": household_state.active_power_w,
        "battery_power_w": battery_state.active_power_w,
        "battery_soc": battery_state.state_of_charge,
        "ev_power_w": ev_state.active_power_w,
        "ev_plugged_in": float(ev_state.plugged_in),
        "ev_charging": float(ev_state.charging),
        "ev_soc": ev_state.state_of_charge,
        "ev_target_soc": ev_state.target_state_of_charge,
        "ev_time_remaining_s": (
            ev_state.time_remaining_s if ev_state.time_remaining_s is not None else -1.0
        ),
        "water_heater_power_w": water_heater_state.active_power_w,
        "water_heater_tank_fraction": water_heater_state.tank_energy_fraction,
        "water_heater_heating": float(water_heater_state.heating),
        "grid_power_w": grid_state.active_power_w,
        "grid_import_price_per_kwh": grid_state.import_price_per_kwh,
        "grid_export_price_per_kwh": grid_state.export_price_per_kwh,
        "grid_cumulative_import_wh": grid_state.cumulative_import_wh,
        "grid_cumulative_export_wh": grid_state.cumulative_export_wh,
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
