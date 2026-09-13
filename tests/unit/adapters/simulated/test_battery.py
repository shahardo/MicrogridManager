import pytest

from microgridmanager.adapters.simulated.battery import SimulatedBatteryAdapter
from microgridmanager.adapters.simulated.clock import SimulationClock


def test_set_active_power_w_round_trips_immediately() -> None:
    battery = SimulatedBatteryAdapter("battery-1")
    battery.set_active_power_w(1234.0)
    assert battery.get_state().active_power_w == 1234.0


def test_charging_increases_state_of_charge() -> None:
    battery = SimulatedBatteryAdapter(
        "battery-1", capacity_wh=10_000.0, rated_power_w=2_000.0, initial_soc=0.5
    )
    battery.set_active_power_w(-2_000.0)
    battery.step(3600.0)

    assert battery.get_state_of_charge() > 0.5


def test_discharging_decreases_state_of_charge() -> None:
    battery = SimulatedBatteryAdapter(
        "battery-1", capacity_wh=10_000.0, rated_power_w=2_000.0, initial_soc=0.5
    )
    battery.set_active_power_w(2_000.0)
    battery.step(3600.0)

    assert battery.get_state_of_charge() < 0.5


def test_state_of_charge_never_exceeds_full_when_overcharging() -> None:
    battery = SimulatedBatteryAdapter(
        "battery-1", capacity_wh=1_000.0, rated_power_w=5_000.0, initial_soc=0.95
    )
    battery.set_active_power_w(-5_000.0)

    for _ in range(10):
        battery.step(3600.0)
        assert 0.0 <= battery.get_state_of_charge() <= 1.0

    assert battery.get_state_of_charge() == pytest.approx(1.0)


def test_state_of_charge_never_goes_below_empty_when_overdischarging() -> None:
    battery = SimulatedBatteryAdapter(
        "battery-1", capacity_wh=1_000.0, rated_power_w=5_000.0, initial_soc=0.05
    )
    battery.set_active_power_w(5_000.0)

    for _ in range(10):
        battery.step(3600.0)
        assert 0.0 <= battery.get_state_of_charge() <= 1.0

    assert battery.get_state_of_charge() == pytest.approx(0.0)


def test_applied_power_clamped_to_rated_power() -> None:
    battery = SimulatedBatteryAdapter(
        "battery-1", capacity_wh=100_000.0, rated_power_w=1_000.0, initial_soc=0.5
    )
    battery.set_active_power_w(9_000.0)
    battery.step(60.0)

    assert battery.get_state().active_power_w <= 1_000.0 + 1e-6


def test_round_trip_efficiency_loses_energy_on_charge_then_discharge() -> None:
    battery = SimulatedBatteryAdapter(
        "battery-1",
        capacity_wh=10_000.0,
        rated_power_w=1_000.0,
        initial_soc=0.5,
        round_trip_efficiency=0.9,
    )
    starting_energy_wh = battery.get_state_of_charge() * 10_000.0

    battery.set_active_power_w(-1_000.0)
    battery.step(3600.0)
    charged_energy_wh = battery.get_state_of_charge() * 10_000.0
    assert charged_energy_wh - starting_energy_wh == pytest.approx(900.0)

    battery.set_active_power_w(1_000.0)
    battery.step(3600.0)
    final_energy_wh = battery.get_state_of_charge() * 10_000.0
    assert final_energy_wh < starting_energy_wh


def test_timestamp_reflects_shared_clock() -> None:
    clock = SimulationClock()
    battery = SimulatedBatteryAdapter("battery-1", clock=clock)
    clock.tick()
    assert battery.get_state().timestamp == clock.now
