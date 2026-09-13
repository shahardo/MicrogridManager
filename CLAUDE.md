# CLAUDE.md

Guidance for Claude Code (or any agent) working in this repository.

## Project

MicrogridManager is a **Distributed Microgrid Management System (DMMS)**. See
[`docs/architecture.md`](docs/architecture.md) for the full conceptual design: a
three-tier (site → regional → utility) hierarchical architecture that scales from
a single microgrid to utility-wide orchestration of many microgrids, unifies real
hardware control and simulation behind one control interface, and assumes
multi-tenant ownership with intermittent connectivity between tiers.

Current status: design phase. No production code exists yet; implementation
follows the phased roadmap in `docs/architecture.md` §6, starting with a
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
    implement without changing it.
- `tests/` — `unit/`, `integration/`, `scenarios/` (added as milestones need
  them).
  - `tests/unit/adapters/` — the **adapter contract test suite**: `fakes.py`
    (minimal in-memory test-double adapters), `contracts.py` (reusable
    `assert_*_contract` functions, one per capability), and
    `test_contract_suite.py` (parametrized checks). New adapter
    implementations should be added to this suite's parametrization rather
    than given their own separate contract tests.
- `pyproject.toml` — project config; dependencies managed with `uv`
  (`uv sync --group dev` creates `.venv` and installs everything — never
  install packages globally).
- `Makefile` — `make test` (pytest), `make lint` (ruff); `make run-dashboard`
  / `make run-scenario` are stubs until the dashboard (M4/M9) and simulation
  runner (M2) exist.
