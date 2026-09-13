""""Household day" scenario (M4/M5): the extended six-device simulated
environment — rooftop PV, a battery, a household load, an EV charger, a
water heater, and a grid connection — driven through a scripted 24-hour day.

Like `normal_day`, the economic control logic here is a fixed, explicit
self-consumption script (battery charges from excess PV, discharges to cover
shortfalls) — not the real dispatch engine (M7). The EV charger and water
heater manage their own charging/reheat decisions internally (see their
adapters' docstrings); this scenario just steps them and reads their status.

M5 adds the safety layer in front of all of that: every tick, `step_scenario`
runs the household/EV/water-heater loads' demand through a
`ProtectionController` *before* deciding what they're actually allowed to
draw — that gate is what decides whether the grid connection may exchange
power at all, and it's what sheds/restores loads in priority order
(household load is critical and shed last; the EV charger is the most
deferrable and shed first) if `grid_connected` goes False. See
`docs/architecture.md`'s protection/control-logic section and
`microgridmanager.protection.state_machine` for the state diagram.
"""

from __future__ import annotations

from collections.abc import Callable
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
from microgridmanager.protection import (
    PROTECTION_STATE_CODES,
    ProtectionController,
    SheddableLoad,
)

DEFAULT_START = datetime(2024, 6, 21, tzinfo=timezone.utc)
DEFAULT_STEP_SECONDS = 300.0
DEFAULT_DURATION_HOURS = 24.0
DEFAULT_EV_SESSION_HORIZON_DAYS = 365
BATTERY_CAPACITY_WH = 13_500.0
BATTERY_RATED_POWER_W = 5_000.0

# Load-shedding priorities, lowest-is-most-critical (see SheddableLoad):
# household circuits (fridge, lights, ...) stay on longest; the EV charger,
# being the most deferrable, is shed first.
HOUSEHOLD_LOAD_PRIORITY = 0
WATER_HEATER_PRIORITY = 1
EV_CHARGER_PRIORITY = 2


def grid_outage_between(outage_start: datetime, outage_end: datetime) -> Callable[[datetime], bool]:
    """Build a `grid_connected_at` callable for `run()` that reports the grid
    down for `[outage_start, outage_end)` and up otherwise — the easiest way
    to script an "outage + black-start + restoration" scenario."""

    def grid_connected_at(now: datetime) -> bool:
        return not (outage_start <= now < outage_end)

    return grid_connected_at


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
    protection: ProtectionController


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
        capacity_wh=BATTERY_CAPACITY_WH,
        rated_power_w=BATTERY_RATED_POWER_W,
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
    protection = ProtectionController()

    return HouseholdScenarioAssets(
        clock=clock,
        pv=pv,
        battery=battery,
        household_load=household_load,
        ev_charger=ev_charger,
        water_heater=water_heater,
        grid=grid,
        protection=protection,
    )


def step_scenario(
    assets: HouseholdScenarioAssets, dt_seconds: float, *, grid_connected: bool = True
) -> dict:
    """Advance one control step.

    Order matters here: the M5 protection decision is made *before* any load
    is stepped, using each load's demand as of the top of the tick (the
    household load's profile is always exactly current; the EV charger's and
    water heater's demand estimates are one tick stale, since their own
    step() is what would update them — an acceptable persistence-style
    estimate for a shedding decision, not a physical shortcoming). Shed loads
    then have their setpoint forced to zero *before* `step()` runs, so the
    decision actually takes effect rather than just being reported.

    After that: the EV charger and water heater integrate themselves, the
    scripted battery self-consumption rule runs against the (possibly
    shed-reduced) total load, and the grid connection absorbs the residual —
    but only if the protection decision allows grid exchange this tick;
    otherwise the PCC is treated as physically open and carries zero power
    regardless of any residual imbalance.

    Returns a flat dict of this step's readings, the visible per-tick record
    every later milestone (and the M4 dashboard) reads.
    """
    # Clear any override a previous tick's shed left in place *before*
    # reading demand: household_load's own get_state() reflects the override
    # if one is set, so reading it first would see the last tick's forced 0 W
    # instead of this tick's real profile-driven demand — which, since
    # household load is the only critical-priority load, was silently
    # zeroing critical_demand_w every other tick and made the state machine
    # flap between ISLANDED and BLACK_START indefinitely instead of
    # recovering for real once resources returned.
    assets.household_load.clear_override()

    pv_state = assets.pv.get_state()
    household_state = assets.household_load.get_state()
    ev_demand_w = assets.ev_charger.get_state().active_power_w
    water_heater_demand_w = assets.water_heater.get_state().active_power_w

    # An estimate for protection decision-making, not a full power-flow
    # calculation: how much the battery can plausibly deliver this tick is
    # capped both by its rated power and by the energy it actually has left
    # (soc * capacity) — the adapter's own step() enforces the same energy
    # limit when it actually applies a setpoint. Capping by rated power alone
    # (ignoring how little energy is left near empty) is what caused the
    # state machine to flap between ISLANDED and BLACK_START every tick once
    # the battery ran low: it kept reporting the full rated power "available"
    # the instant soc ticked above zero, only to immediately drain back to
    # zero once that was actually attempted.
    dt_hours = dt_seconds / 3600.0
    battery_soc = assets.battery.get_state_of_charge()
    battery_energy_wh = battery_soc * BATTERY_CAPACITY_WH
    battery_available_discharge_w = (
        min(BATTERY_RATED_POWER_W, battery_energy_wh / dt_hours) if dt_hours > 0.0 else 0.0
    )
    available_power_w = pv_state.active_power_w + battery_available_discharge_w

    loads = [
        SheddableLoad(
            name="household_load",
            priority=HOUSEHOLD_LOAD_PRIORITY,
            demand_w=household_state.active_power_w,
        ),
        SheddableLoad(
            name="water_heater", priority=WATER_HEATER_PRIORITY, demand_w=water_heater_demand_w
        ),
        SheddableLoad(name="ev_charger", priority=EV_CHARGER_PRIORITY, demand_w=ev_demand_w),
    ]
    decision = assets.protection.decide(
        grid_connected=grid_connected, available_power_w=available_power_w, loads=loads
    )

    if not decision.served["household_load"]:
        assets.household_load.set_active_power_w(0.0)
        household_state = assets.household_load.get_state()

    if decision.served["ev_charger"]:
        assets.ev_charger.clear_override()
    else:
        assets.ev_charger.set_active_power_w(0.0)
    assets.ev_charger.step(dt_seconds)
    ev_state = assets.ev_charger.get_state()

    assets.water_heater.set_shed(not decision.served["water_heater"])
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
    assets.grid.set_net_power_w(residual_w if decision.grid_exchange_allowed() else 0.0)
    assets.grid.step(dt_seconds)
    grid_state = assets.grid.get_state()

    return {
        "timestamp": assets.clock.now.isoformat(),
        "pv_power_w": pv_state.active_power_w,
        "household_load_power_w": household_state.active_power_w,
        "household_load_served": float(decision.served["household_load"]),
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
        "ev_charger_served": float(decision.served["ev_charger"]),
        "water_heater_power_w": water_heater_state.active_power_w,
        "water_heater_tank_fraction": water_heater_state.tank_energy_fraction,
        "water_heater_heating": float(water_heater_state.heating),
        "water_heater_served": float(decision.served["water_heater"]),
        "grid_power_w": grid_state.active_power_w,
        "grid_import_price_per_kwh": grid_state.import_price_per_kwh,
        "grid_export_price_per_kwh": grid_state.export_price_per_kwh,
        "grid_cumulative_import_wh": grid_state.cumulative_import_wh,
        "grid_cumulative_export_wh": grid_state.cumulative_export_wh,
        "grid_connected": float(grid_connected),
        "protection_state": float(PROTECTION_STATE_CODES[decision.state]),
    }


def run(
    *,
    step_seconds: float = DEFAULT_STEP_SECONDS,
    duration_hours: float = DEFAULT_DURATION_HOURS,
    start: datetime | None = None,
    grid_connected_at: Callable[[datetime], bool] | None = None,
) -> list[dict]:
    """Run the scenario. `grid_connected_at`, if given, is evaluated against
    the clock each tick to drive the M5 protection state machine — e.g.
    `grid_outage_between(t0, t1)` for an outage/black-start/restoration
    demonstration. Defaults to an always-connected grid (M4's behavior)."""
    assets = build_scenario(start=start, step_seconds=step_seconds)
    num_steps = int(duration_hours * 3600.0 / step_seconds)
    grid_connected_at = grid_connected_at or (lambda _now: True)

    rows = []
    for _ in range(num_steps):
        grid_connected = grid_connected_at(assets.clock.now)
        rows.append(step_scenario(assets, step_seconds, grid_connected=grid_connected))
        assets.clock.tick()

    return rows
