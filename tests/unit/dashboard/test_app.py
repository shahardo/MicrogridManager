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


def test_index_page_is_served(engine_and_store) -> None:
    engine, store = engine_and_store
    app = create_app(engine, store, tick_interval_seconds=NO_BACKGROUND_TICKS)

    with TestClient(app) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert "MicrogridManager" in response.text
