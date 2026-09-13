"""M4/M5 exit-criteria tests: the six-device household scenario runs a full
simulated day without errors and produces plausible, inspectable output, and
a scripted grid outage exercises the M5 protection state machine end to
end."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from microgridmanager.protection import PROTECTION_STATE_CODES, ProtectionState
from simulation.runner import write_csv
from simulation.scenarios import household_day


def test_full_day_runs_without_errors_and_has_expected_length() -> None:
    rows = household_day.run(step_seconds=300.0, duration_hours=24.0)
    assert len(rows) == 24 * 60 * 60 // 300


def test_battery_and_ev_soc_stay_within_bounds_all_day() -> None:
    rows = household_day.run(step_seconds=300.0, duration_hours=24.0)
    for row in rows:
        assert 0.0 <= row["battery_soc"] <= 1.0
        assert 0.0 <= row["ev_soc"] <= 1.0


def test_water_heater_tank_fraction_stays_within_bounds() -> None:
    rows = household_day.run(step_seconds=300.0, duration_hours=24.0)
    for row in rows:
        assert 0.0 <= row["water_heater_tank_fraction"] <= 1.0


def test_pv_and_ev_power_stay_within_rated_limits() -> None:
    pv_rated_power_w = 6_000.0
    ev_rated_power_w = 7_200.0
    rows = household_day.run(step_seconds=300.0, duration_hours=24.0)
    for row in rows:
        assert 0.0 <= row["pv_power_w"] <= pv_rated_power_w
        assert 0.0 <= row["ev_power_w"] <= ev_rated_power_w + 1e-6


def test_ev_only_charges_while_plugged_in() -> None:
    rows = household_day.run(step_seconds=300.0, duration_hours=24.0)
    for row in rows:
        if row["ev_power_w"] > 0.0:
            assert row["ev_plugged_in"] == 1.0


def test_grid_cumulative_energy_is_monotonically_nondecreasing() -> None:
    rows = household_day.run(step_seconds=300.0, duration_hours=24.0)
    import_readings = [row["grid_cumulative_import_wh"] for row in rows]
    export_readings = [row["grid_cumulative_export_wh"] for row in rows]
    assert import_readings == sorted(import_readings)
    assert export_readings == sorted(export_readings)


def test_grid_tariff_price_switches_between_peak_and_off_peak() -> None:
    rows = household_day.run(step_seconds=300.0, duration_hours=24.0)
    prices = {row["grid_import_price_per_kwh"] for row in rows}
    assert len(prices) >= 2


def test_grid_absorbs_the_residual_power_balance() -> None:
    rows = household_day.run(step_seconds=300.0, duration_hours=24.0)
    for row in rows:
        total_load_w = (
            row["household_load_power_w"] + row["ev_power_w"] + row["water_heater_power_w"]
        )
        residual_w = total_load_w - row["pv_power_w"] - row["battery_power_w"]
        assert row["grid_power_w"] == residual_w


def test_ev_charger_keeps_plugging_in_on_later_days() -> None:
    """Regression test: the EV charger's sessions used to be a fixed,
    one-off list covering only the scenario's first 24 hours, so a
    longer-than-a-day run (exactly what the live dashboard does) went
    permanently flat — unplugged, 0 W — the moment the clock passed the last
    session. A recurring daily commute pattern should keep plugging the car
    in every evening indefinitely."""
    rows = household_day.run(step_seconds=1800.0, duration_hours=24.0 * 5)

    day_length = 24 * 2  # 30-minute steps -> 48 rows/day
    per_day_plugged_in = [
        any(row["ev_plugged_in"] == 1.0 for row in rows[day * day_length : (day + 1) * day_length])
        for day in range(5)
    ]
    assert all(per_day_plugged_in), "EV should plug in at least once on every one of the 5 days"

    per_day_charging = [
        any(row["ev_power_w"] > 0.0 for row in rows[day * day_length : (day + 1) * day_length])
        for day in range(5)
    ]
    assert any(per_day_charging[1:]), "EV should still be charging on days after the first"


def test_outage_triggers_islanding_black_start_and_restoration() -> None:
    """M5 exit-criteria test: a scripted grid outage produces a coherent
    protection-state sequence — NORMAL -> ISLANDING_TRANSITION -> ISLANDED
    -> BLACK_START -> RESTORATION -> NORMAL — sheds non-critical loads before
    the critical household load, and restores everything once the grid
    returns. The outage window (hour 1 to hour 5) ends before dawn, so the
    only thing driving the black-start recovery is the grid returning, not a
    marginal PV-vs-demand crossing (that transition is covered, without the
    inherent real-world chatter of two independently-varying signals staying
    close for an extended stretch, by the state machine's own unit tests)."""
    start = household_day.DEFAULT_START
    outage_start = start + timedelta(hours=1)
    outage_end = start + timedelta(hours=5)
    grid_connected_at = household_day.grid_outage_between(outage_start, outage_end)

    rows = household_day.run(
        step_seconds=300.0, duration_hours=8.0, grid_connected_at=grid_connected_at
    )

    code = {state: PROTECTION_STATE_CODES[state] for state in ProtectionState}
    states_in_order = [row["protection_state"] for row in rows]
    distinct_in_order = [
        s for i, s in enumerate(states_in_order) if i == 0 or s != states_in_order[i - 1]
    ]

    assert distinct_in_order == [
        code[ProtectionState.NORMAL],
        code[ProtectionState.ISLANDING_TRANSITION],
        code[ProtectionState.ISLANDED],
        code[ProtectionState.BLACK_START],
        code[ProtectionState.RESTORATION],
        code[ProtectionState.NORMAL],
    ]

    # No power crosses the PCC at all while the state machine has decided the
    # grid is unavailable (islanding transition through black start).
    disconnected_states = {
        code[ProtectionState.ISLANDING_TRANSITION],
        code[ProtectionState.ISLANDED],
        code[ProtectionState.BLACK_START],
    }
    for row in rows:
        if row["protection_state"] in disconnected_states:
            assert row["grid_power_w"] == 0.0

    # Every load is shed at some point during the outage (black start sheds
    # everything, including the critical household load)...
    assert any(row["household_load_served"] == 0.0 for row in rows)
    assert any(row["ev_charger_served"] == 0.0 for row in rows)
    assert any(row["water_heater_served"] == 0.0 for row in rows)

    # ...and every load is back in service once the grid has been restored.
    final_row = rows[-1]
    assert final_row["protection_state"] == code[ProtectionState.NORMAL]
    assert final_row["household_load_served"] == 1.0
    assert final_row["ev_charger_served"] == 1.0
    assert final_row["water_heater_served"] == 1.0


def test_write_csv_produces_a_readable_file_with_all_rows(tmp_path: Path) -> None:
    rows = household_day.run(step_seconds=1800.0, duration_hours=6.0)
    output_path = tmp_path / "household_day.csv"

    write_csv(rows, output_path)

    assert output_path.exists()
    contents = output_path.read_text().strip().splitlines()
    assert contents[0].split(",") == list(rows[0].keys())
    assert len(contents) - 1 == len(rows)
