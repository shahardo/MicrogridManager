from datetime import datetime, timezone

import pytest

from microgridmanager.adapters.simulated.clock import SimulationClock
from microgridmanager.adapters.simulated.load import SimulatedLoadAdapter
from microgridmanager.adapters.simulated.profiles import daily_load_profile


def test_default_profile_is_constant() -> None:
    load = SimulatedLoadAdapter("load-1")
    first = load.get_state().active_power_w
    clock = SimulationClock()
    load2 = SimulatedLoadAdapter("load-2", clock=clock)
    clock.tick()
    second = load2.get_state().active_power_w
    assert first == second


def test_profile_driven_output_varies_with_time_of_day() -> None:
    profile = daily_load_profile(
        base_watts=400.0, morning_peak_watts=1_800.0, evening_peak_watts=2_600.0
    )
    morning_clock = SimulationClock(start=datetime(2024, 6, 21, 8, tzinfo=timezone.utc))
    midnight_clock = SimulationClock(start=datetime(2024, 6, 21, 0, tzinfo=timezone.utc))

    morning_load = SimulatedLoadAdapter("load-1", clock=morning_clock, profile=profile)
    midnight_load = SimulatedLoadAdapter("load-2", clock=midnight_clock, profile=profile)

    assert morning_load.get_state().active_power_w > midnight_load.get_state().active_power_w


def test_override_persists_until_cleared() -> None:
    clock = SimulationClock(start=datetime(2024, 6, 21, 8, tzinfo=timezone.utc))
    profile = daily_load_profile(400.0, 1_800.0, 2_600.0)
    load = SimulatedLoadAdapter("load-1", clock=clock, profile=profile)

    load.set_active_power_w(750.0)
    assert load.get_state().active_power_w == 750.0

    load.clear_override()
    assert load.get_state().active_power_w == pytest.approx(profile(clock.now))


def test_override_round_trips_arbitrary_values() -> None:
    load = SimulatedLoadAdapter("load-1")

    load.set_active_power_w(1234.0)
    assert load.get_state().active_power_w == 1234.0

    load.set_active_power_w(-500.0)
    assert load.get_state().active_power_w == pytest.approx(-500.0)
