"""Reusable time-driven profiles for simulated adapters: solar irradiance,
load demand, and generator fuel-consumption curves.

These are plain functions of a `datetime` (or of power/rated-power), kept
independent of any adapter so scenarios can swap in their own without
touching adapter code.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Callable

IrradianceProfile = Callable[[datetime], float]
LoadProfile = Callable[[datetime], float]
FuelCurve = Callable[[float, float], float]  # (active_power_w, rated_power_w) -> liters/hour


def _hour_of_day(at: datetime) -> float:
    return at.hour + at.minute / 60.0 + at.second / 3600.0


def daylight_irradiance_profile(
    sunrise_hour: float = 6.0,
    sunset_hour: float = 18.0,
    peak_irradiance: float = 1.0,
) -> IrradianceProfile:
    """A half-sine irradiance curve: zero before sunrise/after sunset, peaking
    at solar noon between them. Returns a fraction in [0.0, peak_irradiance]."""
    daylight_hours = sunset_hour - sunrise_hour

    def profile(at: datetime) -> float:
        hour = _hour_of_day(at)
        if hour <= sunrise_hour or hour >= sunset_hour:
            return 0.0
        fraction_of_day = (hour - sunrise_hour) / daylight_hours
        return peak_irradiance * math.sin(math.pi * fraction_of_day)

    return profile


def constant_load_profile(watts: float) -> LoadProfile:
    return lambda _at: watts


def daily_load_profile(
    base_watts: float,
    morning_peak_watts: float,
    evening_peak_watts: float,
    morning_hour: float = 8.0,
    evening_hour: float = 19.0,
    peak_width_hours: float = 2.0,
) -> LoadProfile:
    """A base load plus two gaussian bumps around a morning and evening peak
    hour — enough to give a plausible household-shaped demand curve."""

    def profile(at: datetime) -> float:
        hour = _hour_of_day(at)
        morning = morning_peak_watts * math.exp(
            -((hour - morning_hour) ** 2) / (2 * peak_width_hours**2)
        )
        evening = evening_peak_watts * math.exp(
            -((hour - evening_hour) ** 2) / (2 * peak_width_hours**2)
        )
        return base_watts + morning + evening

    return profile


def linear_fuel_curve(no_load_fraction: float = 0.08, full_load_lph: float = 20.0) -> FuelCurve:
    """Approximates a typical diesel genset fuel curve: some fuel burned at
    idle even near-zero output, rising roughly linearly with load fraction."""

    def curve(active_power_w: float, rated_power_w: float) -> float:
        if rated_power_w <= 0 or active_power_w <= 0:
            return 0.0
        load_fraction = max(0.0, min(1.0, active_power_w / rated_power_w))
        return full_load_lph * (no_load_fraction + (1 - no_load_fraction) * load_fraction)

    return curve
