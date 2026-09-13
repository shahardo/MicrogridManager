#!/usr/bin/env python3
"""CLI: inspect a completed scenario run recorded in the M3 telemetry store.

This is Phase 1 M3's visible result. With no arguments beyond --db, it lists
the runs available; given --run-id, it lists that run's series; given both
--run-id and --series, it prints that series' time series table.

Examples:
    python scripts/query_telemetry.py
    python scripts/query_telemetry.py --run-id normal_day-20240101T000000Z
    python scripts/query_telemetry.py --run-id normal_day-20240101T000000Z --series pv_power_w
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from microgridmanager.telemetry import TelemetryStore

DEFAULT_DB = Path("output/telemetry.db")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Query the MicrogridManager telemetry store.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--series", default=None)
    parser.add_argument("--asset-id", default=None)
    args = parser.parse_args(argv)

    if not args.db.exists():
        parser.error(f"telemetry database not found: {args.db} (run a scenario first)")

    with TelemetryStore(args.db) as store:
        if args.run_id is None:
            _print_runs(store)
            return

        if args.series is None:
            _print_series(store, args.run_id)
            return

        _print_samples(store, args.run_id, args.series, args.asset_id)


def _print_runs(store: TelemetryStore) -> None:
    runs = store.list_runs()
    if not runs:
        print("No runs recorded yet.")
        return
    print("Available runs:")
    for run_id in runs:
        print(f"  {run_id}")
    print("\nRe-run with --run-id <run> to see its series.")


def _print_series(store: TelemetryStore, run_id: str) -> None:
    series = store.list_series(run_id)
    if not series:
        print(f"No series recorded for run '{run_id}'.")
        return
    print(f"Series recorded for run '{run_id}':")
    for name in series:
        print(f"  {name}")
    print("\nRe-run with --series <name> to see its time series.")


def _print_samples(store: TelemetryStore, run_id: str, series: str, asset_id: str | None) -> None:
    samples = store.query(run_id=run_id, series=series, asset_id=asset_id)
    if not samples:
        print(f"No samples for run '{run_id}', series '{series}'.")
        return

    print(f"{'timestamp':<26} {'asset_id':<16} {'value':>14}")
    for sample in samples:
        asset_id = sample.asset_id or "-"
        print(f"{sample.timestamp.isoformat():<26} {asset_id:<16} {sample.value:>14.3f}")
    print(f"\n{len(samples)} samples.")


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        # Expected when piped into `head`/`less` and the reader closes early.
        sys.stderr.close()
