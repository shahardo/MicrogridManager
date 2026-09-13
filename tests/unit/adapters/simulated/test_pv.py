from datetime import datetime, timedelta, timezone

import pytest

from microgridmanager.adapters.simulated.clock import SimulationClock
from microgridmanager.adapters.simulated.profiles import daylight_irradiance_profile
from microgridmanager.adapters.simulated.pv import SimulatedPVAdapter


def _clock_at(hour: int, minute: int = 0) -> SimulationClock:
    return SimulationClock(start=datetime(2024, 6, 21, hour, minute, tzinfo=timezone.utc))


def test_pv_output_is_zero_at_night() -> None:
    pv = SimulatedPVAdapter("pv-1", clock=_clock_at(2), rated_power_w=5_000.0)
    assert pv.get_state().active_power_w == 0.0


def test_pv_output_peaks_near_solar_noon() -> None:
    sunrise, sunset = 6.0, 18.0
    noon = (sunrise + sunset) / 2
    clock = _clock_at(int(noon))
    pv = SimulatedPVAdapter(
        "pv-1",
        clock=clock,
        rated_power_w=5_000.0,
        irradiance_profile=daylight_irradiance_profile(sunrise, sunset),
    )
    assert pv.get_state().active_power_w == pytest.approx(5_000.0, rel=1e-6)


def test_pv_output_tracks_irradiance_profile_within_tolerance() -> None:
    rated_power_w = 6_000.0
    profile = daylight_irradiance_profile(sunrise_hour=6.0, sunset_hour=18.0)

    for hour in range(0, 24):
        clock = _clock_at(hour)
        pv = SimulatedPVAdapter(
            "pv-1", clock=clock, rated_power_w=rated_power_w, irradiance_profile=profile
        )
        expected = rated_power_w * profile(clock.now)
        assert pv.get_state().active_power_w == pytest.approx(expected, abs=1e-6)


def test_pv_output_never_exceeds_rated_power() -> None:
    rated_power_w = 4_000.0
    clock = SimulationClock(
        start=datetime(2024, 6, 21, tzinfo=timezone.utc), step=timedelta(minutes=30)
    )
    pv = SimulatedPVAdapter("pv-1", clock=clock, rated_power_w=rated_power_w)

    for _ in range(48):
        assert 0.0 <= pv.get_state().active_power_w <= rated_power_w
        clock.tick()
