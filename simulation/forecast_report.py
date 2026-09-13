"""CLI: M6's visible result — run a scenario, forecast its PV/load series
online with the real `microgridmanager.forecasting` module (both the naive
persistence baseline and the real `SeasonalAverageForecaster` the dashboard
now uses live), and print each model's MAE/MAPE against the actual values.

Lives under `simulation/` (not `scripts/`) like `runner.py`, since it drives
a named scenario rather than only touching the installed package — run it
with `python -m simulation.forecast_report` (or `make forecast-report`).

This is the same evaluation `simulation/live_engine.py` performs one tick at
a time for the dashboard's forecast panel, replayed here over a full
multi-day scenario run so the forecasting module's accuracy — and its
improvement over the M4 dashboard's original persistence placeholder — is
visible from the command line.

Example:
    python -m simulation.forecast_report --scenario household_day --duration-hours 72
"""

from __future__ import annotations

import argparse
from datetime import datetime

from microgridmanager.forecasting import (
    Forecaster,
    HistoricalPoint,
    PersistenceForecaster,
    SeasonalAverageForecaster,
    mean_absolute_error,
    mean_absolute_percentage_error,
)
from simulation.scenarios import household_day, normal_day

SCENARIOS = {
    "normal_day": normal_day.run,
    "household_day": household_day.run,
}

# Which of each scenario's row fields are worth forecasting (PV and load are
# the series the dev plan's M6 exit criteria calls out).
SCENARIO_SERIES = {
    "normal_day": ["pv_power_w", "load_power_w"],
    "household_day": ["pv_power_w", "household_load_power_w"],
}

MODELS: dict[str, Forecaster] = {
    "persistence": PersistenceForecaster(),
    "seasonal_average": SeasonalAverageForecaster(),
}


def forecast_series(rows: list[dict], series: str, forecaster: Forecaster) -> list[float]:
    """Replay `forecaster` online against `rows`' actual values for `series`,
    the same way `live_engine.tick()` does: each row's forecast only ever
    sees the history recorded *before* that row."""
    history: list[HistoricalPoint] = []
    forecasts = []
    for row in rows:
        timestamp = datetime.fromisoformat(row["timestamp"])
        actual = row[series]
        forecasts.append(forecaster.predict(history, timestamp, fallback=actual))
        history.append(HistoricalPoint(timestamp=timestamp, value=actual))
    return forecasts


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Report forecast accuracy over a scenario run.")
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), default="household_day")
    parser.add_argument("--step-seconds", type=float, default=300.0)
    parser.add_argument("--duration-hours", type=float, default=72.0)
    args = parser.parse_args(argv)

    rows = SCENARIOS[args.scenario](
        step_seconds=args.step_seconds, duration_hours=args.duration_hours
    )

    print(f"Scenario '{args.scenario}': {len(rows)} steps over {args.duration_hours}h\n")
    print(f"{'series':<28} {'model':<18} {'MAE (W)':>10} {'MAPE (%)':>10}")
    for series in SCENARIO_SERIES[args.scenario]:
        actual = [row[series] for row in rows]
        for model_name, forecaster in MODELS.items():
            forecasts = forecast_series(rows, series, forecaster)
            mae = mean_absolute_error(actual, forecasts)
            try:
                mape_str = f"{mean_absolute_percentage_error(actual, forecasts):>10.2f}"
            except ValueError:
                mape_str = f"{'n/a':>10}"
            print(f"{series:<28} {model_name:<18} {mae:>10.2f} {mape_str}")


if __name__ == "__main__":
    main()
