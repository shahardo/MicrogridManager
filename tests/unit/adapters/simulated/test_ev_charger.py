from datetime import datetime, timedelta, timezone

import pytest

from microgridmanager.adapters.simulated.clock import SimulationClock
from microgridmanager.adapters.simulated.ev_charger import EVSession, SimulatedEVChargerAdapter

START = datetime(2024, 6, 21, tzinfo=timezone.utc)


def test_not_plugged_in_outside_any_session() -> None:
    clock = SimulationClock(start=START)
    charger = SimulatedEVChargerAdapter("ev-1", clock=clock, sessions=[])

    state = charger.get_state()
    assert state.plugged_in is False
    assert state.charging is False
    assert state.time_remaining_s is None


def test_plugged_in_and_charges_below_target() -> None:
    session = EVSession(plug_in=START, deadline=START + timedelta(hours=8), target_soc=0.9)
    clock = SimulationClock(start=START)
    charger = SimulatedEVChargerAdapter(
        "ev-1", clock=clock, initial_soc=0.4, sessions=[session]
    )

    charger.step(3600.0)
    state = charger.get_state()

    assert state.plugged_in is True
    assert state.charging is True
    assert state.active_power_w > 0.0
    assert state.state_of_charge > 0.4
    assert state.target_state_of_charge == 0.9
    # step() integrates state of charge but doesn't advance the clock itself
    # (the scenario ticks it), so time remaining is still measured from START.
    assert state.time_remaining_s == pytest.approx(8 * 3600.0)


def test_stops_charging_once_target_reached() -> None:
    session = EVSession(plug_in=START, deadline=START + timedelta(hours=8), target_soc=0.5)
    clock = SimulationClock(start=START)
    charger = SimulatedEVChargerAdapter(
        "ev-1",
        clock=clock,
        capacity_wh=10_000.0,
        rated_power_w=5_000.0,
        initial_soc=0.49,
        sessions=[session],
    )

    for _ in range(4):
        charger.step(300.0)

    state = charger.get_state()
    assert state.state_of_charge >= 0.5
    assert state.charging is False
    assert state.active_power_w == 0.0


def test_state_of_charge_never_exceeds_full() -> None:
    session = EVSession(plug_in=START, deadline=START + timedelta(hours=8), target_soc=1.0)
    clock = SimulationClock(start=START)
    charger = SimulatedEVChargerAdapter(
        "ev-1", clock=clock, capacity_wh=1_000.0, rated_power_w=5_000.0,
        initial_soc=0.95, sessions=[session],
    )

    for _ in range(10):
        charger.step(3600.0)
        assert 0.0 <= charger.get_state_of_charge() <= 1.0

    assert charger.get_state_of_charge() == pytest.approx(1.0)


def test_no_session_covering_now_means_no_auto_charging() -> None:
    session = EVSession(
        plug_in=START + timedelta(hours=2), deadline=START + timedelta(hours=4)
    )
    clock = SimulationClock(start=START)
    charger = SimulatedEVChargerAdapter("ev-1", clock=clock, sessions=[session])

    charger.step(3600.0)
    assert charger.get_state().active_power_w == 0.0


def test_override_setpoint_round_trips_immediately() -> None:
    charger = SimulatedEVChargerAdapter("ev-1")
    charger.set_active_power_w(1234.0)
    assert charger.get_state().active_power_w == 1234.0

    charger.set_active_power_w(-500.0)
    assert charger.get_state().active_power_w == -500.0


def test_override_takes_precedence_over_auto_charging_during_step() -> None:
    session = EVSession(plug_in=START, deadline=START + timedelta(hours=8), target_soc=1.0)
    clock = SimulationClock(start=START)
    charger = SimulatedEVChargerAdapter(
        "ev-1", clock=clock, capacity_wh=10_000.0, rated_power_w=5_000.0,
        initial_soc=0.5, sessions=[session],
    )

    charger.set_active_power_w(1_000.0)
    charger.step(3600.0)

    assert charger.get_state().active_power_w == pytest.approx(1_000.0)
