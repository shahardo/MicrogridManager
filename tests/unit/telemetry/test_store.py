"""M3 exit-criteria tests: write-then-read round trip, and persistence
surviving a process restart (a fresh TelemetryStore reopened on the same
file)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from microgridmanager.telemetry import TelemetrySample, TelemetryStore


def _sample(
    run_id: str, ts: datetime, series: str, value: float, asset_id: str | None = None
) -> TelemetrySample:
    return TelemetrySample(
        run_id=run_id, timestamp=ts, series=series, value=value, asset_id=asset_id
    )


def test_record_and_query_round_trip(tmp_path: Path) -> None:
    store = TelemetryStore(tmp_path / "telemetry.db")
    t0 = datetime(2024, 1, 1, tzinfo=timezone.utc)

    store.record(_sample("run-1", t0, "pv_power_w", 100.0, asset_id="pv-1"))
    store.record(_sample("run-1", t0 + timedelta(minutes=1), "pv_power_w", 200.0, asset_id="pv-1"))
    store.record(_sample("run-1", t0, "battery_soc", 0.5, asset_id="battery-1"))

    results = store.query(run_id="run-1", series="pv_power_w")
    assert [r.value for r in results] == [100.0, 200.0]
    assert all(r.asset_id == "pv-1" for r in results)
    assert all(r.run_id == "run-1" for r in results)

    store.close()


def test_record_many_and_asset_id_filter(tmp_path: Path) -> None:
    store = TelemetryStore(tmp_path / "telemetry.db")
    t0 = datetime(2024, 1, 1, tzinfo=timezone.utc)

    store.record_many(
        [
            _sample("run-1", t0, "active_power_w", 10.0, asset_id="battery-1"),
            _sample("run-1", t0, "active_power_w", 20.0, asset_id="generator-1"),
        ]
    )

    battery_only = store.query(run_id="run-1", series="active_power_w", asset_id="battery-1")
    assert len(battery_only) == 1
    assert battery_only[0].value == 10.0

    store.close()


def test_query_time_range_filter(tmp_path: Path) -> None:
    store = TelemetryStore(tmp_path / "telemetry.db")
    t0 = datetime(2024, 1, 1, tzinfo=timezone.utc)

    for i in range(5):
        store.record(_sample("run-1", t0 + timedelta(minutes=i), "load_power_w", float(i)))

    results = store.query(
        run_id="run-1",
        series="load_power_w",
        start=t0 + timedelta(minutes=1),
        end=t0 + timedelta(minutes=3),
    )
    assert [r.value for r in results] == [1.0, 2.0, 3.0]

    store.close()


def test_list_runs_and_series(tmp_path: Path) -> None:
    store = TelemetryStore(tmp_path / "telemetry.db")
    t0 = datetime(2024, 1, 1, tzinfo=timezone.utc)

    store.record(_sample("run-1", t0, "pv_power_w", 1.0))
    store.record(_sample("run-2", t0, "pv_power_w", 2.0))
    store.record(_sample("run-1", t0, "battery_soc", 0.5))

    assert store.list_runs() == ["run-1", "run-2"]
    assert store.list_series("run-1") == ["battery_soc", "pv_power_w"]

    store.close()


def test_persistence_survives_reopen(tmp_path: Path) -> None:
    """Simulates a process restart: a fresh TelemetryStore instance opened on
    the same file must see everything a previous instance wrote."""
    db_path = tmp_path / "telemetry.db"
    t0 = datetime(2024, 1, 1, tzinfo=timezone.utc)

    first = TelemetryStore(db_path)
    first.record(_sample("run-1", t0, "pv_power_w", 42.0))
    first.close()

    second = TelemetryStore(db_path)
    results = second.query(run_id="run-1", series="pv_power_w")
    assert len(results) == 1
    assert results[0].value == 42.0
    second.close()


def test_context_manager_closes_connection(tmp_path: Path) -> None:
    db_path = tmp_path / "telemetry.db"
    t0 = datetime(2024, 1, 1, tzinfo=timezone.utc)

    with TelemetryStore(db_path) as store:
        store.record(_sample("run-1", t0, "pv_power_w", 1.0))

    reopened = TelemetryStore(db_path)
    assert reopened.query(run_id="run-1", series="pv_power_w")[0].value == 1.0
    reopened.close()
