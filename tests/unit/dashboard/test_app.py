"""M4 dashboard API tests.

Ticks are driven manually (`engine.tick()`) rather than by letting the app's
background loop run in real time, so these tests are deterministic and fast
— `tick_interval_seconds` is set high enough that the loop never fires
during a test.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from microgridmanager.dashboard.app import create_app
from microgridmanager.telemetry import TelemetryStore
from simulation.live_engine import SimulationEngine

NO_BACKGROUND_TICKS = 3600.0


@pytest.fixture
def engine_and_store(tmp_path):
    store = TelemetryStore(tmp_path / "telemetry.db")
    engine = SimulationEngine(store, step_seconds=300.0)
    yield engine, store
    store.close()


def test_state_reports_defaults_before_any_tick(engine_and_store) -> None:
    engine, store = engine_and_store
    app = create_app(engine, store, tick_interval_seconds=NO_BACKGROUND_TICKS)
    with TestClient(app) as client:
        body = client.get("/api/state").json()

    assert body["running"] is False
    assert body["speed"] == 1.0
    assert body["grid_connected"] is True
    assert body["latest"] is None


def test_history_reflects_manual_ticks(engine_and_store) -> None:
    engine, store = engine_and_store
    for _ in range(5):
        engine.tick()
    app = create_app(engine, store, tick_interval_seconds=NO_BACKGROUND_TICKS)

    with TestClient(app) as client:
        rows = client.get("/api/history?limit=3").json()
        assert len(rows) == 3

        latest = client.get("/api/state").json()["latest"]
        assert latest == rows[-1]


def test_start_and_pause_controls_flip_engine_running_state(engine_and_store) -> None:
    engine, store = engine_and_store
    app = create_app(engine, store, tick_interval_seconds=NO_BACKGROUND_TICKS)

    with TestClient(app) as client:
        body = client.post("/api/controls/start").json()
        assert body["running"] is True
        assert engine.running is True

        body = client.post("/api/controls/pause").json()
        assert body["running"] is False
        assert engine.running is False


def test_reset_control_clears_history_and_assigns_a_new_run_id(engine_and_store) -> None:
    engine, store = engine_and_store
    app = create_app(engine, store, tick_interval_seconds=NO_BACKGROUND_TICKS)

    with TestClient(app) as client:
        engine.tick()
        old_run_id = engine.run_id

        body = client.post("/api/controls/reset").json()
        assert body["run_id"] != old_run_id
        assert len(engine.history) == 0


def test_speed_control_accepts_positive_and_rejects_non_positive(engine_and_store) -> None:
    engine, store = engine_and_store
    app = create_app(engine, store, tick_interval_seconds=NO_BACKGROUND_TICKS)

    with TestClient(app) as client:
        body = client.post("/api/controls/speed", json={"speed": 3.5}).json()
        assert body["speed"] == 3.5
        assert engine.speed == 3.5

        response = client.post("/api/controls/speed", json={"speed": 0.0})
        assert response.status_code == 422


def test_grid_toggle_control_updates_engine_state(engine_and_store) -> None:
    engine, store = engine_and_store
    app = create_app(engine, store, tick_interval_seconds=NO_BACKGROUND_TICKS)

    with TestClient(app) as client:
        body = client.post("/api/controls/grid", json={"connected": False}).json()
        assert body["grid_connected"] is False
        assert engine.grid_connected is False


def test_runs_and_series_endpoints_reflect_recorded_ticks(engine_and_store) -> None:
    engine, store = engine_and_store
    for _ in range(3):
        engine.tick()
    app = create_app(engine, store, tick_interval_seconds=NO_BACKGROUND_TICKS)

    with TestClient(app) as client:
        runs = client.get("/api/runs").json()
        assert engine.run_id in runs

        series = client.get(f"/api/runs/{engine.run_id}/series").json()
        assert "pv_power_w" in series
        assert "battery_soc" in series


def test_unknown_run_id_returns_404(engine_and_store) -> None:
    engine, store = engine_and_store
    app = create_app(engine, store, tick_interval_seconds=NO_BACKGROUND_TICKS)

    with TestClient(app) as client:
        response = client.get("/api/runs/does-not-exist/data")
        assert response.status_code == 404


def test_battery_override_control_updates_engine_and_next_ticks_reading(engine_and_store) -> None:
    engine, store = engine_and_store
    app = create_app(engine, store, tick_interval_seconds=NO_BACKGROUND_TICKS)

    with TestClient(app) as client:
        body = client.post("/api/controls/override/battery", json={"power_w": 1_000.0}).json()
        assert body["battery_power_w"] == 1_000.0

        row = engine.tick()
        assert row["battery_override_active"] == 1.0
        assert row["battery_override_applied"] == 1.0
        assert row["battery_power_w"] == pytest.approx(1_000.0, rel=1e-3)

        latest = client.get("/api/state").json()["latest"]
        assert latest["battery_override_applied"] == 1.0

        # Clearing (power_w=None) reverts to automatic control.
        client.post("/api/controls/override/battery", json={"power_w": None})
        row = engine.tick()
        assert row["battery_override_active"] == 0.0


def test_ev_and_water_heater_override_controls_update_engine(engine_and_store) -> None:
    engine, store = engine_and_store
    app = create_app(engine, store, tick_interval_seconds=NO_BACKGROUND_TICKS)

    with TestClient(app) as client:
        client.post("/api/controls/override/ev_charger", json={"power_w": 500.0})
        client.post("/api/controls/override/water_heater", json={"force_heating": True})

        row = engine.tick()
        assert row["ev_override_active"] == 1.0
        assert row["ev_override_applied"] == 1.0
        assert row["ev_power_w"] == pytest.approx(500.0, rel=1e-3)
        assert row["water_heater_override_active"] == 1.0
        assert row["water_heater_override_applied"] == 1.0
        assert row["water_heater_heating"] == 1.0


def test_overrides_are_rejected_while_grid_disconnected(engine_and_store) -> None:
    """The manual-override endpoints are just a thin wrapper over the
    engine — the actual safety decision is the protection gate's, and this
    proves it's really being consulted rather than the API blindly applying
    whatever was posted."""
    engine, store = engine_and_store
    app = create_app(engine, store, tick_interval_seconds=NO_BACKGROUND_TICKS)

    with TestClient(app) as client:
        client.post("/api/controls/override/battery", json={"power_w": 1_000.0})
        client.post("/api/controls/grid", json={"connected": False})

        # NORMAL -> ISLANDING_TRANSITION -> ISLANDED: by the third tick the
        # site is off-grid and the override should be visibly rejected.
        for _ in range(3):
            row = engine.tick()

        assert row["battery_override_active"] == 1.0
        assert row["battery_override_applied"] == 0.0


def test_devices_endpoint_reports_simulated_only_adapters(engine_and_store) -> None:
    engine, store = engine_and_store
    app = create_app(engine, store, tick_interval_seconds=NO_BACKGROUND_TICKS)

    with TestClient(app) as client:
        devices = client.get("/api/devices").json()

    device_ids = {d["device"] for d in devices}
    assert {"battery", "ev_charger", "water_heater", "pv", "household_load", "grid"} == device_ids
    for d in devices:
        assert d["adapter_mode"] == "simulated"
        assert d["available_modes"] == ["simulated"]


def test_outage_control_disconnects_and_auto_reconnects(engine_and_store) -> None:
    engine, store = engine_and_store
    app = create_app(engine, store, tick_interval_seconds=NO_BACKGROUND_TICKS)

    with TestClient(app) as client:
        body = client.post("/api/controls/outage", json={"duration_minutes": 10.0}).json()
        assert body["duration_minutes"] == 10.0
        assert engine.grid_connected is False

        row = engine.tick()  # tick at t=0: still within the outage window
        assert row["outage_disturbance_active"] == 1.0
        assert engine.grid_connected is False

        row = engine.tick()  # tick at t=+5min: still within the outage window
        assert row["outage_disturbance_active"] == 1.0
        assert engine.grid_connected is False

        row = engine.tick()  # tick at t=+10min: the outage has now expired
        assert row["outage_disturbance_active"] == 0.0
        assert engine.grid_connected is True


def test_load_disturbance_control_scales_household_demand(engine_and_store) -> None:
    engine, store = engine_and_store
    app = create_app(engine, store, tick_interval_seconds=NO_BACKGROUND_TICKS)
    engine.tick()

    with TestClient(app) as client:
        body = client.post(
            "/api/controls/disturbance/load", json={"value": 2.0, "duration_minutes": None}
        ).json()
        assert body["multiplier"] == 2.0

        row = engine.tick()
        assert row["load_disturbance_active"] == 1.0
        assert row["household_load_power_w"] > 0.0

        client.post("/api/controls/disturbance/load", json={"value": None})
        row = engine.tick()
        assert row["load_disturbance_active"] == 0.0


def test_pv_disturbance_control_scales_pv_output(engine_and_store) -> None:
    engine, store = engine_and_store
    app = create_app(engine, store, tick_interval_seconds=NO_BACKGROUND_TICKS)

    with TestClient(app) as client:
        body = client.post(
            "/api/controls/disturbance/pv", json={"value": 0.25, "duration_minutes": None}
        ).json()
        assert body["multiplier"] == 0.25

        row = engine.tick()
        assert row["pv_disturbance_active"] == 1.0
        assert row["pv_disturbance_multiplier"] == pytest.approx(0.25)


def test_demand_response_control_caps_grid_import(engine_and_store) -> None:
    engine, store = engine_and_store
    app = create_app(engine, store, tick_interval_seconds=NO_BACKGROUND_TICKS)

    with TestClient(app) as client:
        body = client.post(
            "/api/controls/disturbance/demand_response",
            json={"value": 100.0, "duration_minutes": None},
        ).json()
        assert body["max_import_w"] == 100.0

        row = engine.tick()
        assert row["demand_response_active"] == 1.0
        assert row["demand_response_cap_w"] == pytest.approx(100.0)


def test_index_page_is_served(engine_and_store) -> None:
    engine, store = engine_and_store
    app = create_app(engine, store, tick_interval_seconds=NO_BACKGROUND_TICKS)

    with TestClient(app) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert "MicrogridManager" in response.text
