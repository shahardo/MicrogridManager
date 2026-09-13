from datetime import datetime, timedelta, timezone

from microgridmanager.adapters.simulated.clock import SimulationClock


def test_default_clock_starts_at_zero_elapsed() -> None:
    clock = SimulationClock()
    assert clock.elapsed_seconds == 0.0
    assert clock.now == clock.start


def test_tick_advances_now_by_one_step() -> None:
    start = datetime(2024, 6, 1, tzinfo=timezone.utc)
    clock = SimulationClock(start=start, step=timedelta(minutes=5))

    clock.tick()

    assert clock.now == start + timedelta(minutes=5)
    assert clock.elapsed_seconds == 300.0


def test_multiple_ticks_accumulate() -> None:
    start = datetime(2024, 6, 1, tzinfo=timezone.utc)
    clock = SimulationClock(start=start, step=timedelta(minutes=15))

    for _ in range(4):
        clock.tick()

    assert clock.now == start + timedelta(hours=1)


def test_reset_returns_to_start() -> None:
    clock = SimulationClock()
    clock.tick()
    clock.tick()

    clock.reset()

    assert clock.now == clock.start
    assert clock.elapsed_seconds == 0.0
