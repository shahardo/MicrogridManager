""""Household day" scenario (M4/M5/M7): the extended six-device simulated
environment — rooftop PV, a battery, a household load, an EV charger, a
water heater, and a grid connection — driven through a scripted 24-hour day.

Like `normal_day`, water heater reheat timing is self-managed (see its
adapter's docstring); this scenario just steps it and reads its status,
treating it as an exogenous, forecasted must-serve load rather than a
dispatch-controlled one (a deliberate, documented scope cut — see
`microgridmanager.dispatch.engine`'s module docstring).

M5 adds the safety layer in front of everything: every tick, `step_scenario`
runs the household/EV/water-heater loads' demand through a
`ProtectionController` *before* deciding what they're actually allowed to
draw — that gate is what decides whether the grid connection may exchange
power at all, and it's what sheds/restores loads in priority order
(household load is critical and shed last; the EV charger is the most
deferrable and shed first) if `grid_connected` goes False. See
`docs/architecture.md`'s protection/control-logic section and
`microgridmanager.protection.state_machine` for the state diagram.

M7 adds the real economic dispatch engine, active only while the protection
decision allows grid exchange (`decision.grid_exchange_allowed()` — i.e.
`NORMAL`/`RESTORATION`): the battery's charge/discharge and the EV charger's
charging power are chosen by `microgridmanager.dispatch.DispatchEngine`'s
rolling-horizon LP instead of the fixed self-consumption rule, using PV/load
forecasts and the real tariff. While islanded/black-starting, there's no
grid to optimize against, so both fall back to the original resilience-first
self-consumption rule (charge from surplus, discharge to cover shortfall,
EV auto-charges within whatever protection allows) — dispatch and
resilience are different jobs, not one engine wearing two hats.

M9 adds `ManualOverrides`: an operator's requested battery/EV power setpoints
and water-heater force-heating decision (from the dashboard's controls
panel), applied on top of whichever of the above (dispatch or the fallback
rule) would otherwise run — but only where `_override_allowed` says the
protection gate currently permits it, so an override can never bypass M5's
safety decisions; it can only act within whatever those decisions already
allow. Every tick reports whether each override was active and whether it
was actually applied, so a rejected override is visible rather than silently
dropped (see the `*_override_*` row fields below).

Post-M9 adds `ScenarioDisturbances`: dashboard controls for injecting
environmental/external disturbances into a *running* simulation, distinct
from `ManualOverrides` (which requests a specific device setpoint) — a
disturbance instead perturbs the scenario's own inputs and lets automatic
control (protection/dispatch/the fallback rule) react to it, which is the
point: an operator wants to see how the system *responds* to a cloud front
or a utility demand-response event, not to dictate the response itself.
Three disturbances, each `None`/inactive by default:

- `load_multiplier` — scales household demand (e.g. 1.5 for "high demand",
  0.5 for "low demand") before it ever reaches the protection/shedding
  calculation, so a scaled-up demand can genuinely trigger shedding under an
  outage exactly like a real demand swing would.
- `pv_multiplier` — scales PV output via `SimulatedPVAdapter.
  set_output_multiplier` (e.g. 0.2 for heavy cloud cover) — the "clouds"
  control.
- `max_import_w` — a demand-response cap on grid import, passed straight
  into `DispatchEngine.dispatch()`'s `max_import_w` parameter so the LP
  proactively shifts to battery/EV usage around the constrained window
  instead of a post-hoc clamp. Only takes effect while dispatch is actually
  running (`dispatch_active`) — while islanded/black-starting there's no
  grid exchange to cap in the first place, and while `dispatch_enabled=False`
  there's no LP to hand the constraint to (the naive fallback rule has no
  notion of a grid cap at all — a documented scope cut, not an oversight).

Each field pairs with a `*_until` expiry (`datetime | None`) so a live
dashboard control can be "trigger for N minutes" rather than needing a
separate clear step — `_disturbance_active` below is the one place that
checks `now` against `until`. Unlike `ManualOverrides`, whose active/applied
outcome depends on the protection gate, a disturbance is simply active or
not (protection still runs and may still shed loads in response to it, same
as it would to any other demand/supply swing) — so the row fields below
report activity, not an applied/rejected distinction.
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
from microgridmanager.dispatch import (
    BatteryState,
    DispatchEngine,
    DispatchInfeasibleError,
    EVChargingState,
)
from microgridmanager.protection import (
    PROTECTION_STATE_CODES,
    ProtectionController,
    ProtectionDecision,
    SheddableLoad,
)

DEFAULT_START = datetime(2024, 6, 21, tzinfo=timezone.utc)
DEFAULT_STEP_SECONDS = 300.0
DEFAULT_DURATION_HOURS = 24.0
DEFAULT_EV_SESSION_HORIZON_DAYS = 365
BATTERY_CAPACITY_WH = 13_500.0
BATTERY_RATED_POWER_W = 5_000.0
EV_CAPACITY_WH = 60_000.0
EV_RATED_POWER_W = 7_200.0

# Load-shedding priorities, lowest-is-most-critical (see SheddableLoad):
# household circuits (fridge, lights, ...) stay on longest; the EV charger,
# being the most deferrable, is shed first.
HOUSEHOLD_LOAD_PRIORITY = 0
WATER_HEATER_PRIORITY = 1
EV_CHARGER_PRIORITY = 2


@dataclass(frozen=True)
class ManualOverrides:
    """One tick's pending operator overrides (M9), from the dashboard's
    controls panel. `None` for a field means "no override, let dispatch/the
    fallback rule decide as usual". A non-`None` value is applied only while
    `_override_allowed` says the protection gate currently permits it for
    that device — see the module docstring."""

    battery_power_w: float | None = None
    ev_power_w: float | None = None
    water_heater_force_heating: bool | None = None


def _override_allowed(decision: ProtectionDecision, served_key: str | None = None) -> bool:
    """Whether a manual setpoint override may be applied this tick.

    Only while the site is grid-connected (`NORMAL`/`RESTORATION` —
    `decision.grid_exchange_allowed()`): while islanded or black-starting,
    the resilience-first fallback rule has full authority over the
    battery/EV/water heater to keep whatever critical loads it can powered,
    and an operator's override must never be able to second-guess it — the
    same "final gate" invariant M5 enforces against automated control
    applies to manual control too. And, for a load that can be shed
    (`served_key` — `None` for the battery, which isn't a `SheddableLoad`),
    never while protection has actually shed it, even though that can't
    currently happen in a grid-connected state — an override can act only
    within what the safety gate already allows, never around it.
    """
    if not decision.grid_exchange_allowed():
        return False
    if served_key is not None and not decision.served.get(served_key, True):
        return False
    return True


@dataclass(frozen=True)
class ScenarioDisturbances:
    """One tick's pending environmental/external disturbances, from the
    dashboard's controls panel — see the module docstring for how these
    differ from `ManualOverrides`. Each value pairs with a `*_until`
    expiry; `None` for a value (regardless of its `*_until`) means "no
    disturbance, use the scenario's normal input"."""

    load_multiplier: float | None = None
    load_multiplier_until: datetime | None = None
    pv_multiplier: float | None = None
    pv_multiplier_until: datetime | None = None
    max_import_w: float | None = None
    max_import_w_until: datetime | None = None


def _disturbance_active(value: float | None, until: datetime | None, now: datetime) -> bool:
    """Whether a `ScenarioDisturbances` field is currently in effect: set,
    and either open-ended (`until is None`) or not yet expired."""
    return value is not None and (until is None or now < until)


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
    dispatch: DispatchEngine


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
        capacity_wh=EV_CAPACITY_WH,
        rated_power_w=EV_RATED_POWER_W,
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
    dispatch = DispatchEngine(step_seconds=step_seconds)

    return HouseholdScenarioAssets(
        clock=clock,
        pv=pv,
        battery=battery,
        household_load=household_load,
        ev_charger=ev_charger,
        water_heater=water_heater,
        grid=grid,
        protection=protection,
        dispatch=dispatch,
    )


def _ev_dispatch_state(
    ev_charger: SimulatedEVChargerAdapter, now: datetime, step_seconds: float, horizon_steps: int
) -> EVChargingState:
    """Translate the EV charger's known session schedule (`sessions` — see
    its docstring) into the generic `EVChargingState` the dispatch engine
    consumes: which horizon steps it's plugged in, and, for whichever step
    contains each session's deadline (if that falls within the horizon at
    all), the state of charge it must reach by then."""
    sessions = ev_charger.sessions
    plugged_in = []
    target_soc_by_step: dict[int, float] = {}
    for k in range(horizon_steps):
        step_start = now + timedelta(seconds=step_seconds * k)
        step_end = now + timedelta(seconds=step_seconds * (k + 1))
        session = next((s for s in sessions if s.plug_in <= step_start < s.deadline), None)
        plugged_in.append(session is not None)
        if session is not None and step_start < session.deadline <= step_end:
            target_soc_by_step[k] = session.target_soc

    return EVChargingState(
        state_of_charge=ev_charger.get_state_of_charge(),
        capacity_wh=EV_CAPACITY_WH,
        rated_power_w=EV_RATED_POWER_W,
        plugged_in=plugged_in,
        target_soc_by_step=target_soc_by_step,
    )


def step_scenario(
    assets: HouseholdScenarioAssets,
    dt_seconds: float,
    *,
    grid_connected: bool = True,
    dispatch_enabled: bool = True,
    overrides: ManualOverrides | None = None,
    disturbances: ScenarioDisturbances | None = None,
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

    The water heater is stepped next (it's exogenous to dispatch either way,
    self-managed or shed), so its real post-step reading is available for
    the M7 dispatch engine's combined "must-serve load" input. Then: while
    the protection decision allows grid exchange (NORMAL/RESTORATION), the
    battery and EV charger's setpoints for this tick come from
    `assets.dispatch`'s rolling-horizon economic plan; otherwise (islanded or
    black-starting — no grid to optimize against) both fall back to the
    original resilience-first self-consumption rule, with the EV simply
    auto-charging (or being shed) like before M7. Finally the grid connection
    absorbs the residual — but only if grid exchange is allowed this tick;
    otherwise the PCC is treated as physically open and carries zero power
    regardless of any residual imbalance.

    `overrides` (M9) is applied at the exact point each device's setpoint
    would otherwise be written — after dispatch/the fallback rule has
    already decided what it *would* do, so an override always has a
    well-defined automatic decision to fall back to when the protection gate
    rejects it (`_override_allowed`), rather than needing a separate pass
    that could race this tick's protection decision.

    `disturbances`, if given, perturb the scenario's own inputs (household
    demand, PV output, a grid-import cap) *before* protection/dispatch ever
    see them, so automatic control reacts to the disturbance the same way it
    would to a real one — see `ScenarioDisturbances`' docstring for why this
    is a different mechanism from `overrides`.

    Returns a flat dict of this step's readings, the visible per-tick record
    every later milestone (and the M4 dashboard) reads.
    """
    overrides = overrides or ManualOverrides()
    disturbances = disturbances or ScenarioDisturbances()
    now = assets.clock.now

    load_disturbance_active = _disturbance_active(
        disturbances.load_multiplier, disturbances.load_multiplier_until, now
    )
    pv_disturbance_active = _disturbance_active(
        disturbances.pv_multiplier, disturbances.pv_multiplier_until, now
    )
    demand_response_active = _disturbance_active(
        disturbances.max_import_w, disturbances.max_import_w_until, now
    )
    # Always (re)set the multiplier, active or not, so clearing a disturbance
    # (or letting it expire) restores the profile-driven output exactly —
    # the adapter itself has no notion of "no disturbance was requested this
    # tick" to fall back to on its own.
    assets.pv.set_output_multiplier(disturbances.pv_multiplier if pv_disturbance_active else 1.0)

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
    if load_disturbance_active:
        # Applied via the load adapter's existing override mechanism (the
        # same one `overrides` would use for other loads) rather than a new
        # adapter method — scaling *this tick's* profile-driven demand, so
        # it tracks the profile's own shape (e.g. still peaks in the
        # evening) instead of pinning demand to a fixed level.
        assets.household_load.set_active_power_w(
            household_state.active_power_w * disturbances.load_multiplier
        )
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

    # M9: an active water-heater override is applied on top of the shed
    # decision above — but `set_shed` (just called) always wins inside the
    # adapter regardless, so gating on `_override_allowed` here exists to
    # report the override as rejected, not to prevent an unsafe write.
    water_heater_override_active = overrides.water_heater_force_heating is not None
    water_heater_override_applied = water_heater_override_active and _override_allowed(
        decision, "water_heater"
    )
    assets.water_heater.set_manual_heating_override(
        overrides.water_heater_force_heating if water_heater_override_applied else None
    )
    assets.water_heater.step(dt_seconds)
    water_heater_state = assets.water_heater.get_state()

    ev_override_active = overrides.ev_power_w is not None
    ev_override_applied = ev_override_active and _override_allowed(decision, "ev_charger")
    battery_override_active = overrides.battery_power_w is not None
    battery_override_applied = battery_override_active and _override_allowed(decision)

    dispatch_active = dispatch_enabled and decision.grid_exchange_allowed()
    dispatch_projected_cost = 0.0
    if dispatch_active:
        # Guaranteed true whenever grid exchange is allowed — see
        # ProtectionController._shed_decision — so there's no separate
        # "served but not dispatched" case to handle here.
        assert decision.served["ev_charger"]

        ev_dispatch_state = _ev_dispatch_state(
            assets.ev_charger, assets.clock.now, dt_seconds, assets.dispatch.horizon_steps
        )
        battery_dispatch_state = BatteryState(
            state_of_charge=battery_soc,
            capacity_wh=BATTERY_CAPACITY_WH,
            rated_power_w=BATTERY_RATED_POWER_W,
        )
        # A flat cap across the whole horizon is a deliberate simplification
        # — the LP's immediate (index-0) decision is what actually matters
        # each tick, and treating the cap as constant for planning purposes
        # only makes the plan slightly more conservative near the window's
        # edge, never unsafe.
        max_import_w_horizon = (
            [disturbances.max_import_w] * assets.dispatch.horizon_steps
            if demand_response_active
            else None
        )
        dispatch_kwargs = dict(
            now=assets.clock.now,
            pv_actual_w=pv_state.active_power_w,
            load_actual_w=household_state.active_power_w + water_heater_state.active_power_w,
            battery=battery_dispatch_state,
            tariff_at=assets.grid.peek_price,
            ev=ev_dispatch_state,
        )
        try:
            plan = assets.dispatch.dispatch(**dispatch_kwargs, max_import_w=max_import_w_horizon)
        except DispatchInfeasibleError:
            # `_clamped_import_caps` only accounts for the battery's rated
            # power, not its remaining energy or the EV's own target-SoC
            # constraints over the horizon — an aggressive operator-supplied
            # cap can still combine with those to leave no feasible plan.
            # Same rationale as the EV target clamp: a demand-response
            # disturbance must never crash the tick, so fall back to an
            # uncapped plan rather than propagating the error — the cap
            # simply couldn't be honored this tick given everything else
            # going on.
            if max_import_w_horizon is None:
                raise
            plan = assets.dispatch.dispatch(**dispatch_kwargs, max_import_w=None)
        dispatch_projected_cost = plan.projected_cost

        if ev_override_applied:
            assets.ev_charger.set_active_power_w(overrides.ev_power_w)
        else:
            assets.ev_charger.set_active_power_w(plan.ev_setpoint_w or 0.0)
        assets.ev_charger.step(dt_seconds)
        ev_state = assets.ev_charger.get_state()

        # plan.battery_setpoint_w already uses the same sign convention as
        # the adapter (+ = discharge, - = charge) — see DispatchPlan's
        # docstring — so it's applied directly, not negated.
        if battery_override_applied:
            assets.battery.set_active_power_w(overrides.battery_power_w)
        else:
            assets.battery.set_active_power_w(plan.battery_setpoint_w)
        assets.battery.step(dt_seconds)
        battery_state = assets.battery.get_state()
    else:
        if ev_override_applied:
            assets.ev_charger.set_active_power_w(overrides.ev_power_w)
        elif decision.served["ev_charger"]:
            assets.ev_charger.clear_override()
        else:
            assets.ev_charger.set_active_power_w(0.0)
        assets.ev_charger.step(dt_seconds)
        ev_state = assets.ev_charger.get_state()

        uncontrolled_load_w = (
            household_state.active_power_w
            + ev_state.active_power_w
            + water_heater_state.active_power_w
        )
        net_surplus_w = pv_state.active_power_w - uncontrolled_load_w
        if battery_override_applied:
            assets.battery.set_active_power_w(overrides.battery_power_w)
        else:
            assets.battery.set_active_power_w(-net_surplus_w)
        assets.battery.step(dt_seconds)
        battery_state = assets.battery.get_state()

    total_load_w = (
        household_state.active_power_w + ev_state.active_power_w + water_heater_state.active_power_w
    )
    residual_w = total_load_w - pv_state.active_power_w - battery_state.active_power_w
    # Grid eligibility is a protection-state property, independent of
    # whether dispatch itself is enabled — the naive baseline
    # (dispatch_enabled=False) must still trade normally with the grid
    # whenever it's connected, exactly like the pre-M7 self-consumption rule
    # did; only the *choice* of battery/EV setpoints changes with dispatch.
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
        "dispatch_active": float(dispatch_active),
        "dispatch_projected_cost_usd": dispatch_projected_cost,
        # M9 manual overrides: "*_active" is whether an override was pending
        # this tick at all (regardless of outcome), "*_applied" is whether
        # the protection gate actually let it through — active-but-not-
        # applied is a visibly rejected override. The requested value fields
        # are only meaningful while "*_active" is set.
        "battery_override_active": float(battery_override_active),
        "battery_override_power_w": overrides.battery_power_w or 0.0,
        "battery_override_applied": float(battery_override_applied),
        "ev_override_active": float(ev_override_active),
        "ev_override_power_w": overrides.ev_power_w or 0.0,
        "ev_override_applied": float(ev_override_applied),
        "water_heater_override_active": float(water_heater_override_active),
        "water_heater_override_heating": float(overrides.water_heater_force_heating or False),
        "water_heater_override_applied": float(water_heater_override_applied),
        # Simulation disturbances (dashboard controls injecting
        # environmental/external swings into a running simulation — see
        # `ScenarioDisturbances`): always present, active or not, so
        # replay's position-for-position `reconstructRows` stays aligned.
        "load_disturbance_active": float(load_disturbance_active),
        "load_disturbance_multiplier": disturbances.load_multiplier or 1.0,
        "pv_disturbance_active": float(pv_disturbance_active),
        "pv_disturbance_multiplier": disturbances.pv_multiplier or 1.0,
        "demand_response_active": float(demand_response_active),
        "demand_response_cap_w": disturbances.max_import_w or 0.0,
    }


def run(
    *,
    step_seconds: float = DEFAULT_STEP_SECONDS,
    duration_hours: float = DEFAULT_DURATION_HOURS,
    start: datetime | None = None,
    grid_connected_at: Callable[[datetime], bool] | None = None,
    dispatch_enabled: bool = True,
    overrides: ManualOverrides | None = None,
    disturbances: ScenarioDisturbances | None = None,
) -> list[dict]:
    """Run the scenario. `grid_connected_at`, if given, is evaluated against
    the clock each tick to drive the M5 protection state machine — e.g.
    `grid_outage_between(t0, t1)` for an outage/black-start/restoration
    demonstration. Defaults to an always-connected grid (M4's behavior).

    `dispatch_enabled=False` (M7) reverts to the original fixed
    self-consumption rule for the battery/EV even while grid-connected — the
    "naive baseline" `simulation/dispatch_report.py` compares the real
    dispatch engine against.

    `overrides` (M9)/`disturbances`, if given, are applied identically every
    tick — a batch run has no live operator changing their mind mid-run, so
    these are mostly useful for scenario tests exercising the override/
    disturbance gating without driving `step_scenario` tick-by-tick
    themselves."""
    assets = build_scenario(start=start, step_seconds=step_seconds)
    num_steps = int(duration_hours * 3600.0 / step_seconds)
    grid_connected_at = grid_connected_at or (lambda _now: True)

    rows = []
    for _ in range(num_steps):
        grid_connected = grid_connected_at(assets.clock.now)
        rows.append(
            step_scenario(
                assets,
                step_seconds,
                grid_connected=grid_connected,
                dispatch_enabled=dispatch_enabled,
                overrides=overrides,
                disturbances=disturbances,
            )
        )
        assets.clock.tick()

    return rows
