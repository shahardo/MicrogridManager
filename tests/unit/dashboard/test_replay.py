"""M4 exit-criteria test: replaying a stored run through the dashboard's
replay API reproduces the same chart data the original live run produced.

`/api/runs/{run_id}/data` bundles every series the way the dashboard's
replay UI consumes it (one array of {timestamp, value} points per series);
reconstructing rows from it position-by-position, the same way `app.js`'s
`reconstructRows` does, must reproduce exactly what `engine.tick()` returned
live.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from microgridmanager.dashboard.app import create_app
from microgridmanager.telemetry import TelemetryStore
from simulation.live_engine import SimulationEngine


def test_replay_reproduces_original_live_chart_data(tmp_path) -> None:
    store = TelemetryStore(tmp_path / "telemetry.db")
    engine = SimulationEngine(store, step_seconds=300.0)

    expected_rows = [engine.tick() for _ in range(12)]
    run_id = engine.run_id

    app = create_app(engine, store, tick_interval_seconds=3600.0)
    with TestClient(app) as client:
        response = client.get(f"/api/runs/{run_id}/data")
        assert response.status_code == 200
        replay_data = response.json()

    series_names = [key for key in expected_rows[0] if key != "timestamp"]
    assert set(replay_data.keys()) == set(series_names)
    for series in series_names:
        assert len(replay_data[series]) == len(expected_rows)

    for i, expected_row in enumerate(expected_rows):
        for series in series_names:
            point = replay_data[series][i]
            assert point["value"] == pytest.approx(expected_row[series])
            assert datetime.fromisoformat(point["timestamp"]) == datetime.fromisoformat(
                expected_row["timestamp"]
            )

    store.close()
