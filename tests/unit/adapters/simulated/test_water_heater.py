import pytest

from microgridmanager.adapters.simulated.clock import SimulationClock
from microgridmanager.adapters.simulated.water_heater import SimulatedWaterHeaterAdapter


def test_tank_starts_full_and_not_heating() -> None:
    heater = SimulatedWaterHeaterAdapter("wh-1", initial_tank_fraction=1.0)
    state = heater.get_state()
    assert state.tank_energy_fraction == pytest.approx(1.0)
    assert state.heating is False
    assert state.active_power_w == 0.0


def test_draw_depletes_tank() -> None:
    heater = SimulatedWaterHeaterAdapter(
        "wh-1", capacity_wh=4_000.0, draw_profile=lambda _at: 1_000.0
    )
    heater.step(3600.0)
    assert heater.get_state().tank_energy_fraction < 1.0


def test_heating_turns_on_below_low_threshold() -> None:
    heater = SimulatedWaterHeaterAdapter(
        "wh-1",
        capacity_wh=1_000.0,
        rated_power_w=1_000.0,
        initial_tank_fraction=0.5,
        low_fraction=0.4,
        high_fraction=0.9,
        draw_profile=lambda _at: 2_000.0,
    )
    heater.step(3600.0)  # depletes below low_fraction, so heating should kick in

    state = heater.get_state()
    assert state.heating is True
    assert state.active_power_w == 1_000.0


def test_heating_turns_off_at_high_threshold() -> None:
    heater = SimulatedWaterHeaterAdapter(
        "wh-1",
        capacity_wh=1_000.0,
        rated_power_w=5_000.0,
        initial_tank_fraction=0.3,
        low_fraction=0.4,
        high_fraction=0.9,
        draw_profile=lambda _at: 0.0,
    )

    for _ in range(5):
        heater.step(600.0)

    state = heater.get_state()
    assert state.tank_energy_fraction >= 0.9
    assert state.heating is False
    assert state.active_power_w == 0.0


def test_tank_energy_stays_within_bounds() -> None:
    heater = SimulatedWaterHeaterAdapter(
        "wh-1",
        capacity_wh=1_000.0,
        rated_power_w=6_000.0,
        initial_tank_fraction=1.0,
        draw_profile=lambda _at: 10_000.0,
    )

    for _ in range(20):
        heater.step(3600.0)
        assert 0.0 <= heater.get_state().tank_energy_fraction <= 1.0


def test_shed_forces_heating_off_even_below_low_threshold() -> None:
    heater = SimulatedWaterHeaterAdapter(
        "wh-1",
        capacity_wh=1_000.0,
        rated_power_w=1_000.0,
        initial_tank_fraction=0.5,
        low_fraction=0.4,
        high_fraction=0.9,
        draw_profile=lambda _at: 2_000.0,
    )
    heater.set_shed(True)
    heater.step(3600.0)  # would normally drop below low_fraction and start heating

    state = heater.get_state()
    assert state.heating is False
    assert state.active_power_w == 0.0
    # The tank still depletes from the draw even while shed.
    assert state.tank_energy_fraction < 0.5


def test_unshedding_resumes_normal_hysteresis_control() -> None:
    heater = SimulatedWaterHeaterAdapter(
        "wh-1",
        capacity_wh=1_000.0,
        rated_power_w=1_000.0,
        initial_tank_fraction=0.5,
        low_fraction=0.4,
        high_fraction=0.9,
        draw_profile=lambda _at: 2_000.0,
    )
    heater.set_shed(True)
    heater.step(3600.0)
    assert heater.get_state().heating is False

    heater.set_shed(False)
    heater.step(3600.0)
    assert heater.get_state().heating is True


def test_manual_override_forces_heating_on_above_high_threshold() -> None:
    heater = SimulatedWaterHeaterAdapter(
        "wh-1",
        capacity_wh=1_000.0,
        rated_power_w=1_000.0,
        initial_tank_fraction=1.0,
        low_fraction=0.4,
        high_fraction=0.9,
        draw_profile=lambda _at: 0.0,
    )
    heater.set_manual_heating_override(True)
    heater.step(600.0)  # tank is already full, hysteresis alone would stay off

    state = heater.get_state()
    assert state.heating is True
    assert state.active_power_w == 1_000.0


def test_manual_override_forces_heating_off_below_low_threshold() -> None:
    heater = SimulatedWaterHeaterAdapter(
        "wh-1",
        capacity_wh=1_000.0,
        rated_power_w=1_000.0,
        initial_tank_fraction=0.3,
        low_fraction=0.4,
        high_fraction=0.9,
        draw_profile=lambda _at: 0.0,
    )
    heater.set_manual_heating_override(False)
    heater.step(600.0)  # tank is already below low_fraction, hysteresis alone would kick in

    state = heater.get_state()
    assert state.heating is False
    assert state.active_power_w == 0.0


def test_shed_wins_over_a_manual_heating_override() -> None:
    heater = SimulatedWaterHeaterAdapter(
        "wh-1",
        capacity_wh=1_000.0,
        rated_power_w=1_000.0,
        initial_tank_fraction=0.3,
        draw_profile=lambda _at: 0.0,
    )
    heater.set_shed(True)
    heater.set_manual_heating_override(True)
    heater.step(600.0)

    assert heater.get_state().heating is False


def test_clearing_a_manual_override_resumes_normal_hysteresis() -> None:
    heater = SimulatedWaterHeaterAdapter(
        "wh-1",
        capacity_wh=1_000.0,
        rated_power_w=1_000.0,
        initial_tank_fraction=1.0,
        low_fraction=0.4,
        high_fraction=0.9,
        draw_profile=lambda _at: 0.0,
    )
    heater.set_manual_heating_override(True)
    heater.step(600.0)
    assert heater.get_state().heating is True

    heater.set_manual_heating_override(None)
    heater.step(600.0)  # tank is still full, so hysteresis alone should turn it back off
    assert heater.get_state().heating is False


def test_timestamp_reflects_shared_clock() -> None:
    clock = SimulationClock()
    heater = SimulatedWaterHeaterAdapter("wh-1", clock=clock)
    clock.tick()
    assert heater.get_state().timestamp == clock.now
