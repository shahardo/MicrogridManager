# CLAUDE.md

Guidance for Claude Code (or any agent) working in this repository.

## Project

MicrogridManager is a **Distributed Microgrid Management System (DMMS)**. See
[`docs/architecture.md`](docs/architecture.md) for the full conceptual design: a
three-tier (site → regional → utility) hierarchical architecture that scales from
a single microgrid to utility-wide orchestration of many microgrids, unifies real
hardware control and simulation behind one control interface, and assumes
multi-tenant ownership with intermittent connectivity between tiers.

Current status: Phase 1 implementation, M2 (simulated adapters) complete;
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
    `--output`; writes the scenario's per-step readings to CSV
    (`output/normal_day.csv` by default; `output/` is gitignored).
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
  install packages globally). No new runtime dependencies were added for M2
  (kept dependency-light per the project's zero-ops bias — CSV output uses
  only the standard library).
- `Makefile` — `make test` (pytest), `make lint` (ruff), `make run-scenario`
  (runs the M2 normal-day scenario); `make run-dashboard` is a stub until the
  dashboard (M4/M9) exists.
