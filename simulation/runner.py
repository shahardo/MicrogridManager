"""CLI entry point: run a named scenario, write its time series to CSV, and
record every reading into the M3 telemetry store.

This is Phase 1 M2's first tangible output (CSV) plus M3's (a queryable,
durable run) — `make run-scenario` (or `python -m simulation.runner`) steps
the simulated PV/battery/load/generator adapters through a scripted day and
leaves both a CSV any spreadsheet/plotting tool can open, and a telemetry run
`scripts/query_telemetry.py` can inspect.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from pathlib import Path

from microgridmanager.telemetry import TelemetrySample, TelemetryStore
from simulation.scenarios import normal_day

SCENARIOS = {
    "normal_day": normal_day.run,
}

DEFAULT_OUTPUT_DIR = Path("output")
DEFAULT_TELEMETRY_DB = DEFAULT_OUTPUT_DIR / "telemetry.db"


def write_csv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def record_telemetry(rows: list[dict], run_id: str, store: TelemetryStore) -> None:
    """Every non-timestamp field of every row becomes one telemetry series,
    keyed by this run's id — the scenario/adapter code stays telemetry-
    agnostic; this is the only place that translates its output into the
    store's schema."""
    samples = [
        TelemetrySample(
            run_id=run_id,
            timestamp=datetime.fromisoformat(row["timestamp"]),
            series=key,
            value=value,
        )
        for row in rows
        for key, value in row.items()
        if key != "timestamp"
    ]
    store.record_many(samples)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run a MicrogridManager simulation scenario.")
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), default="normal_day")
    parser.add_argument("--step-seconds", type=float, default=300.0)
    parser.add_argument("--duration-hours", type=float, default=24.0)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--telemetry-db", type=Path, default=DEFAULT_TELEMETRY_DB)
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args(argv)

    run_scenario = SCENARIOS[args.scenario]
    rows = run_scenario(step_seconds=args.step_seconds, duration_hours=args.duration_hours)

    output_path = args.output or (DEFAULT_OUTPUT_DIR / f"{args.scenario}.csv")
    write_csv(rows, output_path)

    run_id = args.run_id or f"{args.scenario}-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    with TelemetryStore(args.telemetry_db) as store:
        record_telemetry(rows, run_id, store)

    print(f"Wrote {len(rows)} steps of scenario '{args.scenario}' to {output_path}")
    print(f"Recorded run '{run_id}' to telemetry store {args.telemetry_db}")


if __name__ == "__main__":
    main()
