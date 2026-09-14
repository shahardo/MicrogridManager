"""M7 exit-criteria tests: the LP always returns a feasible plan (including
when handed an EV target that can't be reached — it clamps rather than
crashing), respects every physical bound, and actually behaves like an
economic dispatch (charges on cheap power, discharges on expensive power)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from microgridmanager.dispatch import BatteryState, DispatchEngine, EVChargingState

START = datetime(2024, 1, 1, tzinfo=timezone.utc)
STEP_SECONDS = 300.0


def _flat_tariff(import_price: float, export_price: float):
    def tariff_at(_at: datetime) -> tuple[float, float]:
        return (import_price, export_price)

    return tariff_at


def _two_tier_tariff(cheap_before: datetime, cheap_price: float, expensive_price: float):
    def tariff_at(at: datetime) -> tuple[float, float]:
        price = cheap_price if at < cheap_before else expensive_price
        return (price, price * 0.4)

    return tariff_at


def test_dispatch_returns_a_plan_of_the_right_shape() -> None:
    engine = DispatchEngine(step_seconds=STEP_SECONDS, horizon_hours=1.0)
    battery = BatteryState(state_of_charge=0.5, capacity_wh=10_000.0, rated_power_w=5_000.0)

    plan = engine.dispatch(
        now=START,
        pv_actual_w=0.0,
        load_actual_w=500.0,
        battery=battery,
        tariff_at=_flat_tariff(0.2, 0.05),
    )

    assert len(plan.battery_power_w) == engine.horizon_steps
    assert len(plan.pv_forecast_w) == engine.horizon_steps
    assert len(plan.load_forecast_w) == engine.horizon_steps
    assert plan.ev_power_w is None
    assert plan.ev_setpoint_w is None
    assert plan.battery_setpoint_w == plan.battery_power_w[0]


def test_battery_power_never_exceeds_rated_power() -> None:
    engine = DispatchEngine(step_seconds=STEP_SECONDS, horizon_hours=2.0)
    battery = BatteryState(state_of_charge=0.5, capacity_wh=10_000.0, rated_power_w=3_000.0)

    plan = engine.dispatch(
        now=START,
        pv_actual_w=20_000.0,  # far more surplus than the battery could ever absorb
        load_actual_w=100.0,
        battery=battery,
        tariff_at=_flat_tariff(0.2, 0.05),
    )

    for power in plan.battery_power_w:
        assert -3_000.0 - 1e-6 <= power <= 3_000.0 + 1e-6


def test_battery_soc_stays_within_bounds_over_the_horizon() -> None:
    engine = DispatchEngine(step_seconds=STEP_SECONDS, horizon_hours=6.0)
    battery = BatteryState(state_of_charge=0.1, capacity_wh=5_000.0, rated_power_w=5_000.0)

    plan = engine.dispatch(
        now=START,
        pv_actual_w=10_000.0,
        load_actual_w=0.0,
        battery=battery,
        tariff_at=_flat_tariff(0.2, 0.05),
    )

    energy_wh = battery.state_of_charge * battery.capacity_wh
    dt_hours = STEP_SECONDS / 3600.0
    for power in plan.battery_power_w:
        # power > 0 is discharge (lossless at the terminals), < 0 is charge
        # (efficiency applied going in) — the same convention the engine's
        # own SoC recursion uses internally.
        if power >= 0.0:
            energy_wh -= power * dt_hours
        else:
            energy_wh += -power * dt_hours * battery.round_trip_efficiency
        # A loose tolerance: the LP solver's own numerical precision plus
        # floating-point accumulation over dozens of steps, not a bound this
        # test expects to be violated in any meaningful way.
        assert -1e-3 <= energy_wh <= battery.capacity_wh + 1e-3


def test_battery_charges_from_surplus_when_prices_are_flat() -> None:
    """With no economic incentive either way, a self-consumption outcome
    (charge the battery from PV surplus rather than exporting it at a lower
    price than it was bought for) should still be what a cost-minimizing LP
    picks, since export price is always set below import price in this
    project's tariffs."""
    engine = DispatchEngine(step_seconds=STEP_SECONDS, horizon_hours=1.0)
    battery = BatteryState(state_of_charge=0.3, capacity_wh=10_000.0, rated_power_w=5_000.0)

    plan = engine.dispatch(
        now=START,
        pv_actual_w=3_000.0,
        load_actual_w=1_000.0,
        battery=battery,
        tariff_at=_flat_tariff(0.2, 0.05),
    )

    assert plan.battery_setpoint_w < 0.0  # charging, not exporting the surplus


def test_battery_prefers_discharging_before_an_expensive_period_it_can_see() -> None:
    """Given a cheap-then-expensive tariff within the horizon and a fully
    charged battery, the LP should hold back from needlessly exporting
    during the cheap period and instead discharge to cover load once prices
    rise — the textbook arbitrage behavior a rolling-horizon economic
    dispatch exists to capture."""
    engine = DispatchEngine(step_seconds=STEP_SECONDS, horizon_hours=2.0)
    battery = BatteryState(state_of_charge=1.0, capacity_wh=5_000.0, rated_power_w=5_000.0)
    cheap_until = START + timedelta(hours=1)

    plan = engine.dispatch(
        now=START,
        pv_actual_w=0.0,
        load_actual_w=1_000.0,
        battery=battery,
        tariff_at=_two_tier_tariff(cheap_until, cheap_price=0.05, expensive_price=0.40),
    )

    # Horizon step k covers target_time = START + STEP*(k+1) (the engine's
    # own convention — see DispatchEngine._forecast_horizon/dispatch), so the
    # last "cheap" step is the one whose target_time still falls strictly
    # before cheap_until, not simply the first `steps_per_hour` entries.
    is_cheap_step = [
        START + timedelta(seconds=STEP_SECONDS * (k + 1)) < cheap_until
        for k in range(engine.horizon_steps)
    ]
    cheap_period_power = [p for p, cheap in zip(plan.battery_power_w, is_cheap_step) if cheap]
    expensive_period_power = [
        p for p, cheap in zip(plan.battery_power_w, is_cheap_step) if not cheap
    ]
    assert cheap_period_power and expensive_period_power  # sanity: both periods present

    # Never export a fully-charged battery into the cheap window just because
    # it's sitting there — hold it for the expensive window instead.
    assert all(power <= 1e-6 for power in cheap_period_power)
    assert any(power > 0.0 for power in expensive_period_power)


def test_ev_reaches_target_soc_by_its_deadline() -> None:
    engine = DispatchEngine(step_seconds=STEP_SECONDS, horizon_hours=4.0)
    battery = BatteryState(state_of_charge=0.5, capacity_wh=10_000.0, rated_power_w=5_000.0)
    deadline_step = 20  # a bit under 2h into a 4h horizon
    ev = EVChargingState(
        state_of_charge=0.2,
        capacity_wh=10_000.0,
        rated_power_w=7_000.0,
        plugged_in=[True] * engine.horizon_steps,
        target_soc_by_step={deadline_step: 0.8},
    )

    plan = engine.dispatch(
        now=START,
        pv_actual_w=0.0,
        load_actual_w=200.0,
        battery=battery,
        tariff_at=_flat_tariff(0.2, 0.05),
        ev=ev,
    )

    assert plan.ev_power_w is not None
    dt_hours = STEP_SECONDS / 3600.0
    energy_wh = ev.state_of_charge * ev.capacity_wh
    for power in plan.ev_power_w[: deadline_step + 1]:
        energy_wh += power * dt_hours
    assert energy_wh / ev.capacity_wh >= 0.8 - 1e-6


def test_ev_charging_only_happens_while_plugged_in() -> None:
    engine = DispatchEngine(step_seconds=STEP_SECONDS, horizon_hours=1.0)
    battery = BatteryState(state_of_charge=0.5, capacity_wh=10_000.0, rated_power_w=5_000.0)
    plugged_in = [i < engine.horizon_steps // 2 for i in range(engine.horizon_steps)]
    ev = EVChargingState(
        state_of_charge=0.1,
        capacity_wh=10_000.0,
        rated_power_w=7_000.0,
        plugged_in=plugged_in,
    )

    plan = engine.dispatch(
        now=START,
        pv_actual_w=0.0,
        load_actual_w=200.0,
        battery=battery,
        tariff_at=_flat_tariff(0.2, 0.05),
        ev=ev,
    )

    assert plan.ev_power_w is not None
    for power, is_plugged_in in zip(plan.ev_power_w, plugged_in, strict=True):
        if not is_plugged_in:
            assert power == pytest.approx(0.0)
        assert power <= ev.rated_power_w + 1e-6


def test_unreachable_ev_target_is_clamped_instead_of_raising() -> None:
    """A target that demands more energy than the charger could ever deliver
    by its deadline must not make the whole tick's dispatch fail — it should
    charge at the best achievable rate instead."""
    engine = DispatchEngine(step_seconds=STEP_SECONDS, horizon_hours=1.0)
    battery = BatteryState(state_of_charge=0.5, capacity_wh=10_000.0, rated_power_w=5_000.0)
    ev = EVChargingState(
        state_of_charge=0.0,
        capacity_wh=100_000.0,  # huge battery
        rated_power_w=1_000.0,  # tiny charger
        plugged_in=[True] * engine.horizon_steps,
        target_soc_by_step={2: 1.0},  # impossible: needs 100,000 Wh in 15 minutes at 1kW
    )

    plan = engine.dispatch(
        now=START,
        pv_actual_w=0.0,
        load_actual_w=0.0,
        battery=battery,
        tariff_at=_flat_tariff(0.2, 0.05),
        ev=ev,
    )

    assert plan.ev_power_w is not None
    for power in plan.ev_power_w[:3]:
        assert power == pytest.approx(1_000.0)


def test_max_import_w_caps_grid_import_across_the_horizon() -> None:
    """A demand-response import cap (the dashboard's disturbance control)
    should bind grid_import for every horizon step, shifting the battery to
    cover the rest of the load instead."""
    engine = DispatchEngine(step_seconds=STEP_SECONDS, horizon_hours=1.0)
    battery = BatteryState(state_of_charge=0.8, capacity_wh=10_000.0, rated_power_w=5_000.0)
    cap_w = 200.0

    plan = engine.dispatch(
        now=START,
        pv_actual_w=0.0,
        load_actual_w=1_500.0,
        battery=battery,
        tariff_at=_flat_tariff(0.2, 0.05),
        max_import_w=[cap_w] * engine.horizon_steps,
    )

    # With PV=0 and load=1500W under a 200W import cap, the shortfall (at
    # least ~1300W) must come from the battery discharging.
    assert plan.battery_setpoint_w >= 1_300.0 - 1e-6


def test_unreachable_import_cap_is_clamped_instead_of_raising() -> None:
    """A demand-response cap tighter than PV + battery's full rated power can
    ever cover must not make the whole tick's dispatch infeasible — it
    should fall back to the minimum grid import actually needed."""
    engine = DispatchEngine(step_seconds=STEP_SECONDS, horizon_hours=1.0)
    battery = BatteryState(state_of_charge=1.0, capacity_wh=10_000.0, rated_power_w=1_000.0)

    plan = engine.dispatch(
        now=START,
        pv_actual_w=0.0,
        load_actual_w=10_000.0,  # far more than PV + battery's rated power can cover
        battery=battery,
        tariff_at=_flat_tariff(0.2, 0.05),
        max_import_w=[0.0] * engine.horizon_steps,
    )

    assert plan.battery_power_w[0] == pytest.approx(1_000.0, rel=1e-6)


def test_history_grows_and_forecasts_do_not_see_the_current_tick() -> None:
    """No seasonal (24h-cycle) match exists yet at any of these ticks, so
    each call's forecast should fall back to the *previous* tick's actual
    (the very first call falls back to its own `pv_actual_w`, since there's
    no history at all yet) — never the current tick's own value, which
    wouldn't be recorded until after this call returns."""
    engine = DispatchEngine(step_seconds=STEP_SECONDS, horizon_hours=1.0)
    battery = BatteryState(state_of_charge=0.5, capacity_wh=10_000.0, rated_power_w=5_000.0)

    previous_actual = None
    for i in range(5):
        now = START + timedelta(seconds=STEP_SECONDS * i)
        actual = 1_000.0 * i
        plan = engine.dispatch(
            now=now,
            pv_actual_w=actual,
            load_actual_w=500.0,
            battery=battery,
            tariff_at=_flat_tariff(0.2, 0.05),
        )
        expected = actual if previous_actual is None else previous_actual
        assert plan.pv_forecast_w[0] == pytest.approx(expected)
        previous_actual = actual
