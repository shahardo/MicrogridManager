# MicrogridManager

A **Distributed Microgrid Management System (DMMS)**: software for managing
microgrids at any scale — from a single small local microgrid, to a regional
cluster, to utility-scale orchestration of many independently-owned
microgrids — using one recurring architectural pattern.

## What is a microgrid?

A locally controllable cluster of generation (solar, wind, generators),
storage (batteries), and load, able to run either grid-connected or
**islanded** (self-sufficient) during an outage.

## What this system does

At every scale, the system provides:

- **Resilience** — islanding, black-start, load prioritization to keep
  critical loads powered through an outage.
- **Optimization** — economic dispatch and storage scheduling to minimize
  cost and maximize use of local renewables.
- **Grid services** — aggregating flexible capacity across microgrids to
  offer demand response, capacity, or regulation to a utility or market.

It supports both real hardware control and a simulation/digital-twin mode
using the same control logic, and is designed for multi-tenant ownership
(independently-owned microgrids) over unreliable, intermittent network links
between sites and any regional/utility coordination layer.

See [`docs/architecture.md`](docs/architecture.md) for the full conceptual
design, including the three-tier architecture, core subsystems, and phased
implementation roadmap.

## Status

**Implementation in progress (Phase 1, M6 — forecasting).** The architecture
and Phase 1 plan are defined (see `docs/architecture.md` §6 and
`docs/phase-1-dev-plan.md`). M1 delivered the canonical asset model and
`AssetAdapter` interface; M2 added simulated PV, battery (BESS), controllable
load, and diesel/gas generator adapters plus a shared discrete-time
simulation clock, and a scripted "normal day" scenario that exercises them
end-to-end; M3 added a durable, queryable telemetry store; M4 added an EV
charger, a water heater, and a grid connection (with a time-of-use tariff) as
three more simulated devices, a six-device "household day" scenario combining
all of them, and a live/replay web dashboard; M5 added the safety-critical
protection state machine — islanding detection, black start, and
priority-ordered load shedding — in front of it all, driven either by the
dashboard's grid connect/disconnect toggle or a scripted outage; M6 adds a
real forecasting module (a `Forecaster` interface plus persistence and
same-time-of-day-average baseline models) that now drives the dashboard's
forecast panel, replacing its original inline placeholder.

## Development setup

Requires Python 3.11+ and [`uv`](https://docs.astral.sh/uv/). All dependencies
are installed into a project-local virtual environment (`.venv`) — nothing is
installed globally.

The repository includes a `Makefile`, but on native Windows PowerShell the
`make` command is often not installed. In that environment, run the underlying
`uv` commands directly instead of `make`.

### macOS / Linux

```bash
uv sync --group dev   # creates .venv and installs dependencies
make test              # runs the test suite via uv run pytest
make lint               # runs ruff via uv run ruff check .
make run-scenario       # runs the "normal day" simulation and writes output/normal_day.csv
```

### Windows PowerShell

```powershell
uv sync --group dev   # creates .venv and installs dependencies
uv run pytest         # run the test suite
uv run ruff check .   # run the linter
uv run python -m simulation.runner  # run the "normal day" scenario
```

If you want to use `make` on Windows, install a GNU Make implementation (for
example via Git Bash, WSL, or a package manager such as `winget`/`choco`/`scoop`).
Native PowerShell does not include `make` by default, so `make test` may fail with
"The term 'make' is not recognized."

## Running a simulation scenario

`make run-scenario` (or, on Windows, `uv run python -m simulation.runner`)
steps a scripted 24-hour scenario through the simulated adapters. Two
scenarios are available (`--scenario`, default `normal_day`):

- **`normal_day`** (M2) — rooftop PV, a battery, a household-shaped load, and
  a backup generator, using a fixed self-consumption control rule (charge the
  battery from excess solar, discharge to cover shortfalls, fall back to the
  generator only if the battery can't keep up).
- **`household_day`** (M4/M5) — the same PV/battery/household load, plus an
  EV charger (plugs in every evening for the night, auto-charges toward a
  target state of charge by the morning, and uses a commute's worth of
  energy each day it's away — so it keeps needing a real charge indefinitely,
  not just on the scenario's first day), a water heater (a virtual tank that
  depletes against a morning/evening hot-water draw and cycles its heating
  element to reheat), and a grid connection with a time-of-use tariff,
  absorbing whatever import/export is left over after the battery — unless
  the M5 protection layer has decided the grid is unavailable, in which case
  nothing crosses the connection at all (see below).

Neither runs the real dispatch engine (that lands in M7) — they exist purely
to produce visible, inspectable output from the simulated physics.

```bash
make run-scenario                                    # normal_day (default)
make run-scenario ARGS="--scenario household_day"     # the M4/M5 six-device scenario
```

### Scripting a grid outage (M5)

`household_day` can simulate a grid outage, driving the protection state
machine through islanding, load shedding, a possible black start, and
restoration once the grid returns:

```bash
make run-scenario ARGS="--scenario household_day --outage-start-hour 1 --outage-duration-hours 4"
```

This disconnects the grid 1 hour into the run for 4 hours. Inspect the
result with `scripts/query_telemetry.py` (see "Querying telemetry" below) —
look at the `protection_state` series (0=normal, 1=islanding transition,
2=islanded, 3=black start, 4=restoration) and the `household_load_served`/
`water_heater_served`/`ev_charger_served` series to see exactly what was
shed and when it came back.

The run writes a CSV time series (default `output/<scenario>.csv`) with every
device's power/status each step — open it in a spreadsheet or plotting tool
to see the day play out. It also records every one of those readings into the
M3 telemetry store (default `output/telemetry.db`) for later inspection —
see "Querying telemetry" below, or view it live in the dashboard's replay
mode (see "Running the dashboard"). Options: `--scenario`, `--step-seconds`,
`--duration-hours`, `--output`, `--telemetry-db`, `--run-id`.

## Querying telemetry

Every recorded run can be inspected after the fact with
`scripts/query_telemetry.py` — called with no `--run-id` it lists the runs
available; with `--run-id` alone it lists that run's series; with both
`--run-id` and `--series` it prints that series' time series.

```bash
make query-telemetry                                            # list recorded runs
make query-telemetry ARGS="--run-id <run>"                       # list that run's series
make query-telemetry ARGS="--run-id <run> --series pv_power_w"   # print a series
```

On Windows PowerShell, run the script directly instead of through `make`,
e.g. `uv run python scripts/query_telemetry.py --run-id <run> --series pv_power_w`.

## Running the dashboard

`make run-dashboard` (or, on Windows,
`uv run python -m simulation.dashboard_runner`) starts a web dashboard at
<http://127.0.0.1:8000> over the M4 `household_day` scenario:

- **Live mode** runs the six-device scenario in the background — Start/
  Pause/Reset controls, a speed multiplier, and a manual grid connect/
  disconnect toggle that (since M5) is real: switching it off actually
  islands the site, sheds loads by priority, and can drive a black start —
  showing each device's status card, the protection state and which loads
  are currently served or shed, and live power/storage-level/tariff/
  forecast/protection charts as it plays out. Every live tick is also
  recorded to the telemetry store, so a live session can be replayed later
  exactly like any other run.
- **Replay mode** lists every run recorded in the telemetry store (from
  `make run-scenario` or a previous live session) and lets you play/pause/
  seek/speed through it using the same device cards and charts.

The forecast panel is now backed by the real M6 forecasting module (see
"Forecasting" below) — no more placeholder. The "decision variables" panel's
battery/tariff cards (battery SoC headroom, the self-consumption rule's
output) still ship with small inline placeholders standing in for the real
dispatch (M7) engine, which doesn't exist yet — that milestone swaps in real
data without changing the panel. The protection state card next to them is
real, not a placeholder, as of M5.

Options: `--host`, `--port`, `--telemetry-db`, `--step-seconds`.

## Forecasting

`microgridmanager.forecasting` (M6) defines a `Forecaster` interface —
`predict(history, target_time, fallback=...) -> float` — and two baseline
implementations:

- **`PersistenceForecaster`** — predicts a series' next value as whatever was
  most recently observed (the M4 dashboard's original inline forecast, now
  behind the real interface).
- **`SeasonalAverageForecaster`** — predicts a series' value as the average
  of the value observed at the same point in each of the last few cycles of
  a configurable `period` (24h by default: "same time of day, averaged over
  the last week"). Falls back to a persistence-style forecast during the
  first cycle, before any seasonal history exists yet.

The dashboard's live engine (`simulation/live_engine.py`) uses
`SeasonalAverageForecaster` for the PV/household-load forecast panel and
chart — the same fields the M4 placeholder used to fill in, so no dashboard
changes were needed.

`make forecast-report ARGS="--scenario household_day --duration-hours 72"`
(or, on Windows, `uv run python -m simulation.forecast_report --scenario
household_day --duration-hours 72`) runs a scenario and prints each model's
MAE/MAPE against the actual PV and household-load series — this is M6's
visible result, and typically shows `SeasonalAverageForecaster` cutting the
error roughly by a third to a half versus plain persistence once a full
day's history has accumulated. Options: `--scenario`
(`normal_day`/`household_day`), `--step-seconds`, `--duration-hours`.

## Development conventions

This project keeps `README.md` and `CLAUDE.md` up to date with every change,
requires a visible result (a test or a UI/output update) for every change,
and tests every change before it's considered done. See `CLAUDE.md` for
details.
