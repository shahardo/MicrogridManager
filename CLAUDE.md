# CLAUDE.md

Guidance for Claude Code (or any agent) working in this repository.

## Project

MicrogridManager is a **Distributed Microgrid Management System (DMMS)**. See
[`docs/architecture.md`](docs/architecture.md) for the full conceptual design: a
three-tier (site → regional → utility) hierarchical architecture that scales from
a single microgrid to utility-wide orchestration of many microgrids, unifies real
hardware control and simulation behind one control interface, and assumes
multi-tenant ownership with intermittent connectivity between tiers.

Current status: Phase 1 implementation, M3 (local telemetry store) complete;
following the phased roadmap in `docs/architecture.md` §6 and
`docs/phase-1-dev-plan.md`, starting with a single-site controller +
simulation.

## Working conventions (always follow these)

1. **Keep `CLAUDE.md` and `README.md` current.** After adding or changing any
   functionality, update both files in the same change: `README.md` for
   user-facing capability/usage, `CLAUDE.md` for anything a future agent
   session needs to know (new modules, conventions, how to run things).
2. **Visibility first.** Every functional change must produce a visible,
   checkable result — either a new/updated automated test that exercises the
   change, or a visible UI/output update (e.g. a dashboard view, CLI output,
   simulation report). No change should land with only internal code and
   nothing to observe or verify.
3. **Always test each update.** Before considering any change done, run the
   relevant tests (and add them if they don't exist yet) and confirm they
   pass. Don't rely on inspection alone.
4. Prefer extending the shared abstractions defined in `docs/architecture.md`
   (canonical asset/adapter interface, real-vs-simulated adapters, the single
   reusable optimization/dispatch engine, the capability-grant authority
   model) over introducing parallel, one-off mechanisms.
5. Safety-critical logic (protection/islanding/black-start state machine)
   must remain deterministic and independent of any network link — never make
   it depend on connectivity to a regional/utility tier.

## Repository layout

- `docs/architecture.md` — the conceptual design and phased roadmap.
- `docs/phase-1-dev-plan.md` — the detailed, milestone-by-milestone Phase 1 plan.
- `src/microgridmanager/` — the installable package (`assets/`, `adapters/`
  with `simulated/`/`real/` splits, `protection/`, `forecasting/`,
  `dispatch/`, `telemetry/`, `dashboard/`) — filled in milestone by milestone
  per the Phase 1 plan.
  - `adapters/interface.py` — **the canonical asset model and `AssetAdapter`
    interface** (M1). The base `AssetAdapter` ABC (`asset_id`, `asset_type`,
    `get_state()`) plus capability mixins (`PowerControllable`,
    `StateOfChargeReadable`, `Switchable`, `MeterReadable`) that concrete
    adapters compose as needed. This is the seam every future adapter (M2
    simulated, M4 EV charger/water heater, M8 real Modbus/SunSpec) must
    implement without changing it — M2 built against it unchanged.
  - `adapters/simulated/` — **M2 simulated adapters**, all driven off one
    shared `SimulationClock` (`clock.py`, discrete time-step, `now`/`tick()`):
    `pv.py` (irradiance-profile driven, not user-settable — no curtailment
    yet), `battery.py` (`PowerControllable` + `StateOfChargeReadable`;
    `set_active_power_w` records the setpoint immediately, `step(dt_seconds)`
    integrates state of charge with round-trip efficiency and clamps applied
    power to rated power / available energy), `load.py` (profile-driven,
    `set_active_power_w` overrides the profile until `clear_override()`),
    `generator.py` (`PowerControllable`; setpoints are clamped to
    `[0, rated_power_w]` immediately — a genset can't reverse or exceed
    nameplate — with a fuel-curve-driven `step()`/`fuel_consumed_l` for
    future dispatch cost accounting, not part of the M1 `GeneratorState`).
    `profiles.py` holds the reusable irradiance/load/fuel-curve generator
    functions. New adapter constructors take only `asset_id` as a positional
    arg (everything else keyword, defaulted) so they drop straight into the
    M1 contract suite's `adapter_cls("asset-1")` parametrization.
- `simulation/` — not part of the installed package; run via
  `python -m simulation.runner` (needs `pythonpath = ["."]` in
  `pyproject.toml`'s pytest config, already set, for tests to import it too).
  - `scenarios/normal_day.py` — builds the M2 adapters on a shared clock and
    steps them through a scripted 24h day using a **fixed, explicit
    self-consumption + backup-generator rule** (charge battery from excess
    PV, discharge to cover shortfalls, generator only above a 1W shortfall
    dead-band). This is *not* the real dispatch engine (M7) — it exists only
    to exercise the M2 adapters end-to-end.
  - `runner.py` — CLI: `--scenario`, `--step-seconds`, `--duration-hours`,
    `--output`, `--telemetry-db`, `--run-id`; writes the scenario's per-step
    readings to CSV (`output/normal_day.csv` by default; `output/` is
    gitignored) **and** records every non-timestamp field of every row into
    the M3 `TelemetryStore` under an auto-generated (or `--run-id`-supplied)
    run id (`output/telemetry.db` by default). `record_telemetry()` is the
    one place that translates a scenario's row-dict output into telemetry
    samples — the scenario/adapter code itself stays telemetry-agnostic.
- `src/microgridmanager/telemetry/store.py` — **the M3 append-only telemetry
  store** (`TelemetryStore`, `TelemetrySample`). SQLite-backed, long/narrow
  schema (`run_id`, `timestamp`, `asset_id` (nullable), `series`, `value`) —
  one row per metric per timestamp, nothing ever updated in place, so new
  series (protection state, forecasts, dispatch decisions) never need a
  schema change. `record()`/`record_many()` to write; `query()` (filterable
  by `asset_id`/time range), `list_runs()`, `list_series()` to read. Reopening
  a `TelemetryStore` on the same file after the process exits sees everything
  previously written — verified by
  `tests/unit/telemetry/test_store.py::test_persistence_survives_reopen`.
  **Every milestone from here on should write through this store** rather
  than inventing a parallel persistence mechanism.
- `scripts/query_telemetry.py` — **M3's visible result**: CLI over the
  telemetry store. No `--run-id` lists runs; `--run-id` alone lists that
  run's series; `--run-id` + `--series` (optionally + `--asset-id`) prints
  that series' time series table. Also runnable via `make query-telemetry
  ARGS="--run-id ... --series ..."`.
- `tests/` — `unit/`, `integration/`, `scenarios/`.
  - `tests/unit/adapters/` — the **adapter contract test suite**: `fakes.py`
    (minimal in-memory test-double adapters), `contracts.py` (reusable
    `assert_*_contract` functions, one per capability), and
    `test_contract_suite.py` (parametrized checks, now covering both the
    fakes and the M2 simulated adapters — `SimulatedGeneratorAdapter` is
    excluded from the generic `PowerControllable` round-trip check since it
    clamps setpoints, and instead gets a dedicated clamp test). New adapter
    implementations should be added to this suite's parametrization rather
    than given their own separate contract tests.
  - `tests/unit/adapters/simulated/` — physics unit tests per M2 adapter
    (SoC bounds/efficiency, PV tracking irradiance, generator fuel curve and
    rated-power clamping, clock advancement).
  - `tests/scenarios/test_normal_day.py` — the M2 exit-criteria test: a full
    simulated day runs without errors and produces plausible output (SoC
    stays in bounds, generator only fires on a genuine shortfall, fuel use is
    monotonic, CSV round-trips correctly).
- `pyproject.toml` — project config; dependencies managed with `uv`
  (`uv sync --group dev` creates `.venv` and installs everything — never
  install packages globally). No new runtime dependencies were added through
  M3 (kept dependency-light per the project's zero-ops bias — CSV output and
  the telemetry store use only the standard library, `csv` and `sqlite3`).
- `Makefile` — `make test` (pytest), `make lint` (ruff), `make run-scenario`
  (runs the M2 normal-day scenario, now also recording to the M3 telemetry
  store), `make query-telemetry ARGS="..."` (M3's CLI); `make run-dashboard`
  is a stub until the dashboard (M4/M9) exists. Native Windows PowerShell
  usually does not ship with `make`, so Windows users should run the
  equivalent `uv run ...` commands directly instead of `make`.
