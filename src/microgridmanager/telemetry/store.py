"""Append-only telemetry store: the durable record of every asset state
sample and control decision a scenario or live controller produces.

A long/narrow schema (one row per metric per timestamp) keeps the store
simple and genuinely append-only — nothing is ever updated in place — and
lets any later milestone (protection state, forecasts, dispatch decisions)
add new series without a schema change. Backed by SQLite so a run's history
survives a process restart with no extra wiring.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS telemetry_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    asset_id TEXT,
    series TEXT NOT NULL,
    value REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_telemetry_run_series_time
    ON telemetry_samples (run_id, series, timestamp);
"""


@dataclass(frozen=True)
class TelemetrySample:
    run_id: str
    timestamp: datetime
    series: str
    value: float
    asset_id: str | None = None


def _to_iso(ts: datetime) -> str:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc).isoformat()


class TelemetryStore:
    """A SQLite-backed, append-only telemetry log.

    Safe to reopen against the same file across process restarts: all state
    lives on disk, nothing is cached only in memory. Also safe to share
    across threads (e.g. the M4 dashboard's background simulation loop
    writing while a request-handling thread reads): the underlying
    connection is guarded by a lock rather than confined to one thread.
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def record(self, sample: TelemetrySample) -> None:
        self.record_many([sample])

    def record_many(self, samples: Iterable[TelemetrySample]) -> None:
        rows = [
            (s.run_id, _to_iso(s.timestamp), s.asset_id, s.series, s.value) for s in samples
        ]
        if not rows:
            return
        with self._lock:
            self._conn.executemany(
                "INSERT INTO telemetry_samples (run_id, timestamp, asset_id, series, value) "
                "VALUES (?, ?, ?, ?, ?)",
                rows,
            )
            self._conn.commit()

    def query(
        self,
        *,
        run_id: str,
        series: str,
        asset_id: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[TelemetrySample]:
        clauses = ["run_id = ?", "series = ?"]
        params: list[object] = [run_id, series]
        if asset_id is not None:
            clauses.append("asset_id = ?")
            params.append(asset_id)
        if start is not None:
            clauses.append("timestamp >= ?")
            params.append(_to_iso(start))
        if end is not None:
            clauses.append("timestamp <= ?")
            params.append(_to_iso(end))

        sql = (
            "SELECT run_id, timestamp, asset_id, series, value FROM telemetry_samples "
            f"WHERE {' AND '.join(clauses)} ORDER BY timestamp ASC"
        )
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [
            TelemetrySample(
                run_id=row[0],
                timestamp=datetime.fromisoformat(row[1]),
                asset_id=row[2],
                series=row[3],
                value=row[4],
            )
            for row in rows
        ]

    def list_runs(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT run_id FROM telemetry_samples ORDER BY run_id"
            ).fetchall()
        return [row[0] for row in rows]

    def list_series(self, run_id: str) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT series FROM telemetry_samples WHERE run_id = ? ORDER BY series",
                (run_id,),
            ).fetchall()
        return [row[0] for row in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> TelemetryStore:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
