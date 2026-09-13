import pytest

from microgridmanager.adapters.simulated.generator import SimulatedGeneratorAdapter
from microgridmanager.adapters.simulated.profiles import linear_fuel_curve


def test_setpoint_clamped_to_rated_power() -> None:
    generator = SimulatedGeneratorAdapter("generator-1", rated_power_w=4_000.0)

    generator.set_active_power_w(9_000.0)
    assert generator.get_state().active_power_w == 4_000.0


def test_setpoint_clamped_to_zero_for_negative_requests() -> None:
    generator = SimulatedGeneratorAdapter("generator-1", rated_power_w=4_000.0)

    generator.set_active_power_w(-500.0)
    assert generator.get_state().active_power_w == 0.0


def test_within_range_setpoint_is_unclamped() -> None:
    generator = SimulatedGeneratorAdapter("generator-1", rated_power_w=4_000.0)

    generator.set_active_power_w(2_500.0)
    assert generator.get_state().active_power_w == 2_500.0


def test_output_never_exceeds_rated_power_across_requests() -> None:
    rated_power_w = 3_000.0
    generator = SimulatedGeneratorAdapter("generator-1", rated_power_w=rated_power_w)

    for requested_w in [-1_000.0, 0.0, 1_500.0, 3_000.0, 6_000.0, 10_000.0]:
        generator.set_active_power_w(requested_w)
        assert 0.0 <= generator.get_state().active_power_w <= rated_power_w


def test_fuel_consumption_increases_with_load() -> None:
    rated_power_w = 4_000.0
    curve = linear_fuel_curve()

    idle_gen = SimulatedGeneratorAdapter("g-idle", rated_power_w=rated_power_w, fuel_curve=curve)
    idle_gen.set_active_power_w(500.0)
    idle_gen.step(3600.0)

    loaded_gen = SimulatedGeneratorAdapter(
        "g-loaded", rated_power_w=rated_power_w, fuel_curve=curve
    )
    loaded_gen.set_active_power_w(4_000.0)
    loaded_gen.step(3600.0)

    assert loaded_gen.fuel_consumed_l > idle_gen.fuel_consumed_l


def test_no_fuel_consumed_when_off() -> None:
    generator = SimulatedGeneratorAdapter("generator-1", rated_power_w=4_000.0)
    generator.set_active_power_w(0.0)
    generator.step(3600.0)
    assert generator.fuel_consumed_l == 0.0


def test_fuel_consumed_accumulates_over_steps() -> None:
    generator = SimulatedGeneratorAdapter("generator-1", rated_power_w=4_000.0)
    generator.set_active_power_w(2_000.0)

    generator.step(1800.0)
    after_first = generator.fuel_consumed_l
    assert after_first > 0.0

    generator.step(1800.0)
    assert generator.fuel_consumed_l == pytest.approx(after_first * 2)
