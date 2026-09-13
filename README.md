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

**Implementation in progress (Phase 1, M2 — simulated adapters).** The
architecture and Phase 1 plan are defined (see `docs/architecture.md` §6 and
`docs/phase-1-dev-plan.md`). M1 delivered the canonical asset model and
`AssetAdapter` interface; M2 adds simulated PV, battery (BESS), controllable
load, and diesel/gas generator adapters plus a shared discrete-time
simulation clock, and a scripted "normal day" scenario that exercises them
end-to-end.

## Development setup

Requires Python 3.11+ and [`uv`](https://docs.astral.sh/uv/). All dependencies
are installed into a project-local virtual environment (`.venv`) — nothing is
installed globally.

```bash
uv sync --group dev   # creates .venv and installs dependencies
make test              # run the test suite (uv run pytest)
make lint               # run the linter (uv run ruff check .)
make run-scenario       # run the "normal day" simulation, writes output/normal_day.csv
```

## Running a simulation scenario

`make run-scenario` (or `uv run python -m simulation.runner`) steps a
scripted 24-hour "normal day" through the M2 simulated adapters — rooftop
PV, a battery, a household-shaped load, and a backup generator — using a
fixed self-consumption control rule (charge the battery from excess solar,
discharge to cover shortfalls, fall back to the generator only if the
battery can't keep up). This is not the real dispatch engine (that lands in
M7) — it exists purely to produce visible, inspectable output from the
simulated physics.

The run writes a CSV time series (default `output/normal_day.csv`) with
each step's PV/load/battery/generator power, battery state of charge, and
cumulative generator fuel use — open it in a spreadsheet or plotting tool to
see the day play out. Options: `--scenario`, `--step-seconds`,
`--duration-hours`, `--output`.

## Development conventions

This project keeps `README.md` and `CLAUDE.md` up to date with every change,
requires a visible result (a test or a UI/output update) for every change,
and tests every change before it's considered done. See `CLAUDE.md` for
details.
