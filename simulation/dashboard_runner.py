"""CLI entry point: launch the M4 dashboard against a live household_day
simulation.

This is the wiring point between the scenario-agnostic, installed
`microgridmanager.dashboard` package and the simulation-only `household_day`
scenario — analogous to how `runner.py` wires a named batch scenario into the
telemetry store. `make run-dashboard` (or `python -m
simulation.dashboard_runner`) starts a FastAPI/uvicorn server serving the
live view plus replay mode over every run (batch or live) recorded in the
telemetry store.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from microgridmanager.dashboard.app import create_app
from microgridmanager.telemetry import TelemetryStore
from simulation.live_engine import DEFAULT_STEP_SECONDS, SimulationEngine

DEFAULT_TELEMETRY_DB = Path("output") / "telemetry.db"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the MicrogridManager dashboard.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--telemetry-db", type=Path, default=DEFAULT_TELEMETRY_DB)
    parser.add_argument("--step-seconds", type=float, default=DEFAULT_STEP_SECONDS)
    args = parser.parse_args(argv)

    store = TelemetryStore(args.telemetry_db)
    engine = SimulationEngine(store, step_seconds=args.step_seconds)
    app = create_app(engine, store)

    print(f"Serving MicrogridManager dashboard on http://{args.host}:{args.port}")
    print(f"Recording live runs to telemetry store {args.telemetry_db}")
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
