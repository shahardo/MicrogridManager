# CLAUDE.md

Guidance for Claude Code (or any agent) working in this repository.

## Project

MicrogridManager is a **Distributed Microgrid Management System (DMMS)**. See
[`docs/architecture.md`](docs/architecture.md) for the full conceptual design: a
three-tier (site → regional → utility) hierarchical architecture that scales from
a single microgrid to utility-wide orchestration of many microgrids, unifies real
hardware control and simulation behind one control interface, and assumes
multi-tenant ownership with intermittent connectivity between tiers.

Current status: Phase 1 implementation, M4 (extended simulated environment &
dashboard, with replay) complete; following the phased roadmap in
`docs/architecture.md` §6 and `docs/phase-1-dev-plan.md`, starting with a
single-site controller + simulation.

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
  - **M4 additions**, behind the same M1 interface (no interface changes):
    `ev_charger.py` (`SimulatedEVChargerAdapter` — `PowerControllable` +
    `StateOfChargeReadable`; a session-based load driven by a list of
    `EVSession(plug_in, deadline, target_soc)`: while plugged in and below
    target it auto-charges at rated power exactly like the generator's
    internal setpoint, but `set_active_power_w` overrides that decision —
    same override pattern as `load.py` — so M7's dispatcher can shape the
    charge curve later without this adapter changing. Also tracks the
    plugged-in → unplugged transition and, if `commute_energy_wh` > 0
    (default 0, no effect), deducts it from the battery once per trip —
    without this, a car already at its session's target has nothing left to
    charge on a later day even if it keeps plugging in on schedule, which is
    what made the EV panel go flat in any live dashboard session running
    past the first simulated day), `water_heater.py`
    (`SimulatedWaterHeaterAdapter` — no capability mixins, self-managing: a
    virtual tank depletes against a `WaterDrawProfile` and the heating
    element cycles on/off with hysteresis between `low_fraction`/
    `high_fraction`), `grid.py` (`SimulatedGridConnectionAdapter` — the PCC
    pseudo-asset; not independently controllable, `set_net_power_w()` records
    whatever residual import(+)/export(-) the scenario computes and `step()`
    integrates cumulative energy; `get_state()` reports the current
    `TariffProfile` price and `peek_price(at)` reads the price at an
    arbitrary time for the dashboard's projected-price decision variable
    without side effects). `profiles.py` gained
    `household_water_draw_profile()` and `time_of_use_tariff_profile()`
    alongside the M2 profiles.
- `simulation/` — not part of the installed package; run via
  `python -m simulation.runner` (needs `pythonpath = ["."]` in
  `pyproject.toml`'s pytest config, already set, for tests to import it too).
  - `scenarios/normal_day.py` — builds the M2 adapters on a shared clock and
    steps them through a scripted 24h day using a **fixed, explicit
    self-consumption + backup-generator rule** (charge battery from excess
    PV, discharge to cover shortfalls, generator only above a 1W shortfall
    dead-band). This is *not* the real dispatch engine (M7) — it exists only
    to exercise the M2 adapters end-to-end.
  - `scenarios/household_day.py` — **M4's six-device scenario**: rooftop PV,
    battery, household load, EV charger, water heater, grid connection, all
    on one clock. Same self-consumption battery rule as `normal_day`, but
    with no generator — whatever residual import/export is left after the
    battery is sent to `grid.set_net_power_w()`. The EV charger and water
    heater manage their own charge/reheat decisions internally (see their
    adapters above); this scenario just steps them and reads status.
    `step_scenario()` returns one flat dict per tick (18 fields — every
    device's power/status plus tariff price) that both `runner.py` (batch)
    and `live_engine.py` (live) feed into the telemetry store unchanged.
    `_daily_ev_sessions(start)` generates a **recurring daily commute
    pattern** (plug in every evening, charge overnight, unplug for the
    morning commute) for a year out, rather than the original one-off list
    of two sessions — the original list only covered the scenario's first
    24 hours, which is invisible in a bounded batch `run()` but meant every
    live dashboard session that ran past its first simulated day saw the EV
    permanently flat (unplugged, 0 W, constant SoC). Paired with the EV
    adapter's `commute_energy_wh` (see above) so the car actually needs each
    night's charge rather than arriving already at target. See
    `tests/scenarios/test_household_day.py::test_ev_charger_keeps_plugging_in_on_later_days`.
  - `runner.py` — CLI: `--scenario` (`normal_day` or `household_day`),
    `--step-seconds`, `--duration-hours`, `--output`, `--telemetry-db`,
    `--run-id`; writes the scenario's per-step readings to CSV
    (`output/<scenario>.csv` by default; `output/` is gitignored) **and**
    records every non-timestamp field of every row into the M3
    `TelemetryStore` under an auto-generated (or `--run-id`-supplied) run id
    (`output/telemetry.db` by default). `record_telemetry()` is the one place
    that translates a scenario's row-dict output into telemetry samples — the
    scenario/adapter code itself stays telemetry-agnostic.
  - `live_engine.py` — **M4's `SimulationEngine`**: drives `household_day`
    one `tick()` at a time (rather than a fixed batch loop) so the dashboard
    can show it running live. Adds the M4 dashboard's placeholder
    forecast/decision-variable fields to each tick's row
    (`pv_power_forecast_w`/`household_load_power_forecast_w` — persistence,
    i.e. last tick's actual, from `microgridmanager.dashboard.placeholders`;
    `battery_soc_headroom`; `charge_rule_output_w`; `grid_connected`;
    `grid_projected_import/export_price_per_kwh` via `grid.peek_price()`)
    before recording to telemetry and appending to a rolling in-memory
    `history` deque the live charts read from. Exposes `start()`/`pause()`/
    `reset()`/`set_speed()`/`set_grid_connected()` for the controls panel.
    Deliberately lives under `simulation/` (not the installed package) since
    it wires up one specific scenario, mirroring `runner.py`'s role for batch
    runs — see its module docstring for the small duck-typed interface
    `microgridmanager.dashboard.app` depends on instead of importing this
    directly, so the installed dashboard package stays scenario-agnostic
    (M9 can point it at a real site controller without changing it).
  - `dashboard_runner.py` — CLI (`python -m simulation.dashboard_runner`,
    `make run-dashboard`) wiring a `TelemetryStore` + `SimulationEngine` into
    `microgridmanager.dashboard.app.create_app()` and serving it with
    `uvicorn`. Flags: `--host`, `--port`, `--telemetry-db`, `--step-seconds`.
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
  than inventing a parallel persistence mechanism. Since M4, the underlying
  SQLite connection is opened with `check_same_thread=False` and every method
  is guarded by a `threading.Lock` — the dashboard's background simulation
  loop (asyncio event-loop thread) and its request handlers (FastAPI's sync
  endpoints run in a thread pool) both touch the same store concurrently.
- `src/microgridmanager/dashboard/` — **M4's dashboard**: a FastAPI service
  (`app.py`) plus a static vanilla-JS/canvas page (`static/`), no frontend
  build step. `create_app(engine, telemetry_store, *, tick_interval_seconds)`
  is intentionally scenario-agnostic — `engine` only needs to satisfy the
  small `LiveEngine` protocol documented in `app.py` (`running`/`speed`/
  `grid_connected`/`run_id`/`history` plus `start()`/`pause()`/`reset()`/
  `set_speed()`/`set_grid_connected()`/`tick()`), so this module never
  imports from `simulation/`; `simulation/dashboard_runner.py` does the
  wiring. Runs a background `asyncio` task (started/stopped via the FastAPI
  `lifespan`) that calls `engine.tick()` on a timer scaled by `engine.speed`
  whenever `engine.running` is true. Endpoints: `GET /api/state` (running/
  speed/grid_connected/run_id/latest reading), `GET /api/history?limit=`
  (recent rows for live charts), `POST /api/controls/{start,pause,reset}`,
  `POST /api/controls/speed` `{speed}`, `POST /api/controls/grid`
  `{connected}` (the manual grid connect/disconnect toggle — no physical
  effect yet, it only drives the placeholder island indicator; M5 wires it to
  the real protection state machine), `GET /api/runs` /
  `GET /api/runs/{run_id}/series` (thin wrappers over `TelemetryStore`), and
  `GET /api/runs/{run_id}/data` (every series for a run, bundled for
  replay). `placeholders.py` holds `persistence_forecast()` — the inline
  stand-in for M6's real forecasting model; M6/M7 replace the *callers* of
  these functions without changing the dashboard panels, since the series
  names stay the same (the tracked placeholder-to-real swaps from the Phase 1
  plan). `static/app.js` polls `/api/state` + `/api/history` every second in
  Live mode; Replay mode lists `/api/runs`, loads one run's
  `/api/runs/{id}/data`, reconstructs per-tick rows by indexing every
  series array position-for-position (`reconstructRows`), and scrubs through
  them client-side (play/pause/seek/speed) reusing the same card/chart
  renderers as the live view — proven to reproduce the live data exactly by
  `tests/unit/dashboard/test_replay.py`.
- `scripts/query_telemetry.py` — **M3's visible result**: CLI over the
  telemetry store. No `--run-id` lists runs; `--run-id` alone lists that
  run's series; `--run-id` + `--series` (optionally + `--asset-id`) prints
  that series' time series table. Also runnable via `make query-telemetry
  ARGS="--run-id ... --series ..."`.
- `tests/` — `unit/`, `integration/`, `scenarios/`.
  - `tests/unit/adapters/` — the **adapter contract test suite**: `fakes.py`
    (minimal in-memory test-double adapters), `contracts.py` (reusable
    `assert_*_contract` functions, one per capability), and
    `test_contract_suite.py` (parametrized checks, now covering the fakes,
    the M2 simulated adapters, and the M4 EV charger/water heater/grid
    adapters — `SimulatedGeneratorAdapter` is excluded from the generic
    `PowerControllable` round-trip check since it clamps setpoints, and
    instead gets a dedicated clamp test; `SimulatedEVChargerAdapter` *is*
    included in that generic check since its override setter round-trips
    unclamped, same as the battery/load adapters). New adapter
    implementations should be added to this suite's parametrization rather
    than given their own separate contract tests.
  - `tests/unit/adapters/simulated/` — physics unit tests per M2/M4 adapter
    (SoC bounds/efficiency, PV tracking irradiance, generator fuel curve and
    rated-power clamping, clock advancement, EV session plug/unplug and
    target-SoC charging, water heater hysteresis and tank bounds, grid
    cumulative import/export energy and tariff timing).
  - `tests/scenarios/test_normal_day.py` — the M2 exit-criteria test: a full
    simulated day runs without errors and produces plausible output (SoC
    stays in bounds, generator only fires on a genuine shortfall, fuel use is
    monotonic, CSV round-trips correctly).
  - `tests/scenarios/test_household_day.py` — the M4 exit-criteria test:
    the six-device day runs without errors (battery/EV SoC and water heater
    tank fraction stay in bounds, PV/EV power stay within rated limits, the
    EV only draws power while plugged in, grid cumulative energy is
    monotonic, the tariff actually switches peak/off-peak, and the grid's
    reported power always equals the residual of every other device's
    balance).
  - `tests/unit/dashboard/` — `test_app.py` drives the FastAPI app's API
    (state/history/controls/runs/series) via `TestClient`, ticking the engine
    manually rather than relying on the real-time background loop so tests
    stay deterministic; `test_replay.py` is the M4 replay exit-criteria test
    — it ticks a `SimulationEngine` directly, captures the exact rows
    returned, then asserts `GET /api/runs/{run_id}/data` reconstructs those
    same rows value-for-value and timestamp-for-timestamp.
- `pyproject.toml` — project config; dependencies managed with `uv`
  (`uv sync --group dev` creates `.venv` and installs everything — never
  install packages globally). No new runtime dependencies were added through
  M3 (kept dependency-light per the project's zero-ops bias — CSV output and
  the telemetry store use only the standard library, `csv` and `sqlite3`).
  M4 adds `fastapi` and `uvicorn[standard]` as runtime dependencies (per the
  Phase 1 tech stack table) and `httpx` to the `dev` group (required by
  FastAPI's `TestClient`).
- `Makefile` — `make test` (pytest), `make lint` (ruff), `make run-scenario`
  (runs the M2 normal-day scenario by default; pass `ARGS="--scenario
  household_day"` for M4's six-device scenario), `make query-telemetry
  ARGS="..."` (M3's CLI), `make run-dashboard` (M4's dashboard — starts a
  uvicorn server at `http://127.0.0.1:8000`). Native Windows PowerShell
  usually does not ship with `make`, so Windows users should run the
  equivalent `uv run ...` commands directly instead of `make`.
