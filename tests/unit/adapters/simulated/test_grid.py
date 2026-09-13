from datetime import datetime, timezone

import pytest

from microgridmanager.adapters.simulated.clock import SimulationClock
from microgridmanager.adapters.simulated.grid import SimulatedGridConnectionAdapter
from microgridmanager.adapters.simulated.profiles import time_of_use_tariff_profile


def test_set_net_power_w_round_trips_immediately() -> None:
    grid = SimulatedGridConnectionAdapter("grid-1")
    grid.set_net_power_w(1500.0)
    assert grid.get_state().active_power_w == 1500.0


def test_importing_accumulates_cumulative_import_energy() -> None:
    grid = SimulatedGridConnectionAdapter("grid-1")
    grid.set_net_power_w(1_000.0)
    grid.step(3600.0)

    state = grid.get_state()
    assert state.cumulative_import_wh == pytest.approx(1_000.0)
    assert state.cumulative_export_wh == 0.0


def test_exporting_accumulates_cumulative_export_energy() -> None:
    grid = SimulatedGridConnectionAdapter("grid-1")
    grid.set_net_power_w(-500.0)
    grid.step(3600.0)

    state = grid.get_state()
    assert state.cumulative_export_wh == pytest.approx(500.0)
    assert state.cumulative_import_wh == 0.0


def test_cumulative_energy_accumulates_across_steps() -> None:
    grid = SimulatedGridConnectionAdapter("grid-1")
    grid.set_net_power_w(1_000.0)
    grid.step(1800.0)
    grid.step(1800.0)

    assert grid.get_state().cumulative_import_wh == pytest.approx(1_000.0)


def test_peek_price_reads_price_at_an_arbitrary_time_without_side_effects() -> None:
    peak = datetime(2024, 6, 21, 18, 0, tzinfo=timezone.utc)
    off_peak = datetime(2024, 6, 21, 3, 0, tzinfo=timezone.utc)
    clock = SimulationClock(start=off_peak)
    grid = SimulatedGridConnectionAdapter(
        "grid-1", clock=clock, tariff_profile=time_of_use_tariff_profile()
    )

    import_price, _export_price = grid.peek_price(peak)
    assert import_price == grid.peek_price(peak)[0]
    # Peeking a future time doesn't change what get_state() reports now.
    assert grid.get_state().import_price_per_kwh != import_price


def test_tariff_price_reflects_time_of_day() -> None:
    peak = datetime(2024, 6, 21, 18, 0, tzinfo=timezone.utc)
    off_peak = datetime(2024, 6, 21, 3, 0, tzinfo=timezone.utc)
    clock = SimulationClock(start=off_peak)
    grid = SimulatedGridConnectionAdapter(
        "grid-1", clock=clock, tariff_profile=time_of_use_tariff_profile()
    )

    off_peak_state = grid.get_state()

    clock.start = peak
    clock.reset()
    peak_state = grid.get_state()

    assert peak_state.import_price_per_kwh > off_peak_state.import_price_per_kwh
