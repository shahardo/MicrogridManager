"""CLI entry point: run a named scenario and write its time series to CSV.

This is Phase 1 M2's first tangible output — `make run-scenario` (or
`python -m simulation.runner`) steps the simulated PV/battery/load/generator
adapters through a scripted day and writes a CSV any spreadsheet or plotting
tool can open.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from simulation.scenarios import normal_day

SCENARIOS = {
    "normal_day": normal_day.run,
}

DEFAULT_OUTPUT_DIR = Path("output")


def write_csv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run a MicrogridManager simulation scenario.")
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), default="normal_day")
    parser.add_argument("--step-seconds", type=float, default=300.0)
    parser.add_argument("--duration-hours", type=float, default=24.0)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)

    run_scenario = SCENARIOS[args.scenario]
    rows = run_scenario(step_seconds=args.step_seconds, duration_hours=args.duration_hours)

    output_path = args.output or (DEFAULT_OUTPUT_DIR / f"{args.scenario}.csv")
    write_csv(rows, output_path)
    print(f"Wrote {len(rows)} steps of scenario '{args.scenario}' to {output_path}")


if __name__ == "__main__":
    main()
