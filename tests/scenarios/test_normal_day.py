"""M2 exit-criteria test: a full simulated day runs without errors and
produces plausible, inspectable output."""

from __future__ import annotations

from pathlib import Path

from simulation.runner import write_csv
from simulation.scenarios import normal_day


def test_full_day_runs_without_errors_and_has_expected_length() -> None:
    rows = normal_day.run(step_seconds=300.0, duration_hours=24.0)
    assert len(rows) == 24 * 60 * 60 // 300


def test_battery_soc_stays_within_bounds_all_day() -> None:
    rows = normal_day.run(step_seconds=300.0, duration_hours=24.0)
    for row in rows:
        assert 0.0 <= row["battery_soc"] <= 1.0


def test_pv_and_generator_power_stay_within_rated_limits() -> None:
    pv_rated_power_w = 6_000.0
    generator_rated_power_w = 4_000.0
    rows = normal_day.run(step_seconds=300.0, duration_hours=24.0)
    for row in rows:
        assert 0.0 <= row["pv_power_w"] <= pv_rated_power_w
        assert 0.0 <= row["generator_power_w"] <= generator_rated_power_w


def test_generator_only_runs_when_pv_and_battery_cannot_cover_load() -> None:
    rows = normal_day.run(step_seconds=300.0, duration_hours=24.0)
    for row in rows:
        if row["generator_power_w"] > 0.0:
            shortfall = row["load_power_w"] - row["pv_power_w"] - row["battery_power_w"]
            assert shortfall > -1e-6


def test_generator_fuel_consumption_is_monotonically_nondecreasing() -> None:
    rows = normal_day.run(step_seconds=300.0, duration_hours=24.0)
    fuel_readings = [row["generator_fuel_consumed_l"] for row in rows]
    assert fuel_readings == sorted(fuel_readings)


def test_write_csv_produces_a_readable_file_with_all_rows(tmp_path: Path) -> None:
    rows = normal_day.run(step_seconds=1800.0, duration_hours=6.0)
    output_path = tmp_path / "normal_day.csv"

    write_csv(rows, output_path)

    assert output_path.exists()
    contents = output_path.read_text().strip().splitlines()
    assert contents[0].split(",") == list(rows[0].keys())
    assert len(contents) - 1 == len(rows)
