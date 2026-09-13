# Phase 1 Development Plan: Single-Site Controller + Simulation

This plan turns `docs/architecture.md` §6 Phase 1 into concrete, sequenced work. It
follows the project's standing conventions from `CLAUDE.md`: every milestone below
ships with a **visible result** (a test suite and/or a UI/output artifact) and is
**tested before being considered done**. `README.md` and `CLAUDE.md` get updated at
the end of each milestone that changes what the project can do or how it's run.

## Goal

A single-site microgrid controller that runs identically against simulated or real
hardware: monitors assets, protects the site (islanding/black-start/load-shedding),
forecasts load and generation, dispatches assets to minimize cost, and exposes a
live dashboard — all validated first in simulation. This is a shippable product on
its own (real deployment or pure planning/sizing tool), and every abstraction it
introduces (asset interface, adapter split, dispatch engine) is reused unmodified
by Phase 2+.

## Tech stack (decisions for Phase 1)

| Concern | Choice | Why |
|---|---|---|
| Language | Python 3.11+ | Strong ecosystem for control/simulation/optimization (numpy, pandas, pymodbus, pulp/scipy) and for the eventual regional/utility services in later phases. |
| Dependency mgmt | `pyproject.toml` + `uv` (or `pip` + `requirements.txt` if `uv` unavailable) | Reproducible, fast installs. |
| Testing | `pytest` | Standard, supports unit/integration/parametrized scenario tests. |
| Lint/format | `ruff` | Single fast tool for both. |
| Optimization | `PuLP` (LP) to start, swappable later for a full MPC/MILP library | Simple, dependency-light, good enough for linear economic dispatch; the dispatch engine is designed so the solver is swappable without touching callers. |
| Real protocol adapter | `pymodbus` (Modbus TCP/RTU) | Most widely deployed DER/inverter protocol; pymodbus also ships a test server, enabling adapter integration tests without physical hardware. |
| Dashboard | `FastAPI` + a minimal server-rendered/HTMX or vanilla JS page (no heavy frontend build) | Fast to stand up, keeps Phase 1 light; can be replaced by a richer frontend later without touching controller code, since the dashboard only reads from the telemetry/state API. |
| Local storage | SQLite (telemetry/event log) | Zero-ops, sufficient for a single site, matches the append-only event-log design in the architecture doc. |

These are defaults chosen to unblock Phase 1; flag if you'd prefer a different
language/stack before implementation starts, since this is the one decision that's
expensive to reverse later.

## Repository layout

```
src/microgridmanager/
  assets/            # canonical asset model (PV, BESS, generator, load, breaker, meter,
                     #   ev_charger, water_heater — the latter two added in M4)
  adapters/
    interface.py     # AssetAdapter abstract interface — the load-bearing seam
    simulated/        # simulated adapters (physics/behavior models), incl. grid tariff feed
    real/              # real adapters (modbus, sunspec)
  protection/         # islanding/black-start/load-shedding state machine
  forecasting/         # load & PV/wind forecast interface + baseline models
  dispatch/            # rolling-horizon economic dispatch engine
  telemetry/           # append-only store, query API
  site_controller.py  # wires the above into one control loop
  dashboard/           # FastAPI app + minimal UI, incl. replay.py (M4)
simulation/
  scenarios/           # YAML/JSON scenario definitions (weather, load profile, EV/water-
                     #   heater schedules, tariff curve, events)
  runner.py            # drives a site_controller against simulated adapters + a scenario
tests/
  unit/
  integration/
  scenarios/           # full day-in-the-life scenario tests
docs/
  phase-1-dev-plan.md  # this file
```

## Testing strategy

- **Unit tests**: one module = one behavior contract (e.g. battery SoC math, state
  machine transition rules, forecast error calculation, dispatch feasibility).
- **Adapter contract tests**: a single shared test suite run against *every*
  adapter implementation (simulated and real) to guarantee they satisfy the same
  interface — this is what makes "real vs. simulated, identical logic" true rather
  than aspirational.
- **Integration tests**: wire real modules together (forecasting → dispatch →
  protection gate → adapters → telemetry) without going through the full scenario
  runner.
- **Scenario tests**: end-to-end, using `simulation/runner.py` against named
  scenarios (normal day, grid outage + black-start + restoration, forecast-driven
  cost savings) — these double as the project's regression suite and its
  demonstration material.
- Every milestone's exit criteria includes `pytest` passing in full, not just its
  own new tests.

## Milestones

Each milestone lists: scope, visible deliverable (per the visibility-first rule),
tests, and exit criteria. Do them in order — each depends on the previous.

### M0 — Project scaffolding
- Set up `pyproject.toml`, `ruff` config, `pytest` config, directory layout above.
- Add a `Makefile` or `scripts/` with `test`, `lint`, `run-dashboard`, `run-scenario`
  entry points.
- **Visible result**: a trivial smoke test (`tests/unit/test_smoke.py`) proving the
  package imports and the test runner works.
- **Exit criteria**: `pytest` and `ruff check` both run clean on a fresh checkout;
  `README.md` updated with "how to install and run tests."

### M1 — Canonical asset model & adapter interface
- Define asset types (`Inverter`, `BatteryStorage`, `Generator`, `Load`, `Breaker`,
  `Meter`) as data models with the capabilities each needs (`get_state`,
  `set_active_power`, `get_state_of_charge`, `open`/`close`, `get_reading`, …).
- Define the abstract `AssetAdapter` interface all adapters (real or simulated)
  must implement.
- Write the **adapter contract test suite** (shared, parametrized over adapter
  implementations) — even though only a trivial in-memory fake adapter exists yet.
- **Visible result**: the contract test suite passing against a minimal fake
  adapter.
- **Exit criteria**: interface is stable enough that M2 can build against it
  without changing it; contract tests documented in `tests/unit/adapters/`.
  (Two more controllable-load kinds — EV charger, water heater — are added in
  M4 behind this same interface, without changing it.)

### M2 — Simulated adapters (core asset types)
- Implement simulated adapters: PV (irradiance-profile driven output), BESS
  (SoC/efficiency/degradation-free-for-now charge-discharge model), generic
  controllable Load (profile-driven), diesel/gas Generator (fuel-curve driven).
- Implement a simple discrete time-step simulation clock shared by all simulated
  adapters.
- **Visible result**: `simulation/runner.py` steps through a scripted day and
  writes a CSV/plot of each asset's power/SoC over time (`make run-scenario`
  produces `output/normal_day.png` or similar) — this is the first tangible output
  of the whole project.
- **Tests**: each simulated adapter passes the M1 contract suite; unit tests for
  the underlying physics (SoC bounds respected, PV output tracks irradiance within
  tolerance, generator won't exceed rated output).
- **Exit criteria**: a full simulated day runs without errors and produces
  plausible, inspectable output.

### M3 — Local telemetry store
- Append-only telemetry log (SQLite) recording every asset state sample and every
  control decision, with a simple query API (time range → series).
- **Visible result**: a CLI command (`scripts/query_telemetry.py`) that prints a
  time series table/plot from a completed scenario run.
- **Tests**: write-then-read round trip; persistence survives process restart
  (reload from disk mid-scenario).
- **Exit criteria**: every milestone from here on writes through this store, so
  every scenario run leaves an inspectable record.

### M4 — Extended simulated environment & dashboard (with replay)
- Extend the M2 simulated environment into a realistic household/rooftop
  scenario with six simulated devices, adding two new controllable-load asset
  kinds behind the **same M1 `AssetAdapter` interface** (no interface changes):
  - **Rooftop PV source** — M2's PV adapter, parameterized for a rooftop array
    (capacity, orientation-adjusted irradiance profile).
  - **Battery storage** — M2's BESS adapter, unchanged.
  - **Household consumer** — M2's generic Load adapter, given a realistic
    household daily profile (morning/evening peaks).
  - **Electric car (EV charger)** — new `EVCharger` asset: a session-based load
    that plugs in/out on a schedule and charges toward a target SoC/energy by a
    deadline; reports plugged/charging state, target, and time remaining.
  - **Water heater** — new `WaterHeater` asset: a thermal-storage-style load
    that maintains a virtual tank's energy level, depletes it against a hot-
    water demand schedule (morning/evening draws), and cycles its heating
    element to reheat.
  - **Outside grid connection** — a `Grid` pseudo-asset at the point of common
    coupling: import/export metering plus a real-time (or time-of-use) tariff
    feed (an import/export price series over the scenario horizon).
- Extend the M3 telemetry schema to capture every device's status fields and
  the tariff price series, so a completed run can be fully replayed later.
- Build the project's dashboard (this pulls forward the dashboard work from
  later in the plan, since visibility is wanted this early): a FastAPI service
  + minimal web page with:
  - **Device status panel**: one card per device (PV, battery, household load,
    EV charger, water heater, grid connection) showing current power, SoC/tank
    level/session state, and tariff price.
  - **Operation charts**: time series of per-device power, battery SoC, water
    heater tank level, EV session progress, and import/export price.
  - **Forecast panel**: load/PV forecast vs. actual. The real forecasting
    engine doesn't exist yet (M6), so this ships with a small inline
    placeholder forecast (e.g. persistence) purely so the panel has real data
    to chart; M6 swaps in the real model without changing the panel.
  - **Decision-variables panel**: the internal signals a controller would use
    — current/projected tariff price, battery SoC headroom, and a placeholder
    charge/discharge rule's output. Also a placeholder grid-connect/island
    indicator. Both are wired to the real protection (M5) and dispatch (M7)
    modules once those land, without changing the panel.
  - **Controls panel**: scenario controls (start/pause/reset, speed
    multiplier) and a manual **grid connect/disconnect toggle** — this becomes
    the input signal M5's protection state machine consumes later.
  - **Replay mode**: load any completed run from the M3 telemetry store and
    scrub through it (play/pause/seek/speed) using the same charts/panels as
    the live view, sourced from historical data — this is what lets past
    behavior be analyzed after the fact.
- **Visible result**: `make run-dashboard` shows all six devices' live status
  and charts updating in a browser; a second mode lets you pick a completed run
  and replay it.
- **Tests**: unit tests for the two new adapters (EV session/target logic,
  water-heater tank depletion/reheat logic) against the M1 contract suite;
  API/schema tests for the new dashboard endpoints; a replay test asserting
  replaying a stored run reproduces the same chart data as the original live
  run.
- **Exit criteria**: the six-device scenario runs end-to-end and is fully
  visible live and in replay; every later milestone (M5–M7) only has to wire
  its new subsystem into an existing panel, not build new UI.

### M5 — Protection & control state machine
- Implement the explicit state machine from the architecture doc: `Normal
  (grid-connected)` ↔ `Islanding-transition` ↔ `Islanded` ↔ `Black-start` ↔
  `Restoration`, plus load-shedding priority tiers.
- Drive it from the grid-connection signal (the M4 dashboard's manual toggle,
  or a scenario event, e.g. "grid fault at t=2h").
- Wire it as the **final gate** in front of every adapter write — no control
  signal reaches an adapter without passing through this layer, per the
  architecture doc's safety invariant.
- Replace the M4 dashboard's placeholder grid-connect/island indicator with
  this module's real state.
- **Visible result**: a scenario ("outage + black-start + restoration") whose
  output plot/log — and now the M4 dashboard, live — clearly shows the state
  transitions over time and which loads were shed and restored, in what order.
- **Tests**: unit tests for every transition's guard condition; a scenario test
  asserting critical loads stay powered throughout a simulated outage and
  non-critical loads shed/restore in the configured priority order.
- **Exit criteria**: the state machine cannot be bypassed by any other module —
  enforced by code structure (adapters only reachable through the protection
  layer's apply path), not by convention.

### M6 — Forecasting
- Define the forecasting interface (historical series in → probabilistic/point
  forecast out).
- Implement a baseline model (e.g. persistence or same-day-last-week average) for
  load and PV.
- Replace the M4 dashboard's placeholder forecast panel with this module's real
  output.
- **Visible result**: a forecast-vs-actual plot/report generated from a scenario
  run, with an error metric (MAE/MAPE) printed, now shown live in the M4
  dashboard's forecast panel.
- **Tests**: forecast accuracy within a defined bound on synthetic data with known
  patterns; interface contract test so future ML-based models are swappable.
- **Exit criteria**: dispatch (M7) can consume forecasts without knowing which
  model produced them.

### M7 — Optimization / dispatch engine
- Implement the rolling-horizon economic dispatch engine (LP via PuLP to start):
  minimize cost given forecasted load/PV, battery constraints, and the M4
  `Grid` asset's tariff signal; output per-asset setpoints for the next
  control window (including EV charging and water-heater reheat timing).
- Wire it into `site_controller.py`'s main loop: forecast → dispatch → protection
  gate → adapters → telemetry, on a fixed tick.
- Replace the M4 dashboard's placeholder decision-variables panel with this
  module's real dispatch decisions.
- **Visible result**: a scenario comparing "dispatch-optimized day" vs. "naive
  baseline day" (e.g. battery/EV/water-heater never coordinated with price vs.
  optimized), reporting the cost difference — shown in the M4 dashboard and
  its replay mode.
- **Tests**: dispatch always returns a feasible plan given feasible constraints;
  constraint violations (e.g. requesting more than rated power) are rejected/
  clamped before reaching adapters; integration test for the full control loop
  tick.
- **Exit criteria**: full day-long scenario runs the real control loop (not just
  simulated physics) and produces a cost report.

### M8 — Real adapters (Modbus / SunSpec)
- Implement a Modbus-based real adapter satisfying the same `AssetAdapter`
  interface, targeting one common device profile (e.g. a SunSpec-compliant
  inverter/BESS register map).
- Use `pymodbus`'s test server to run the adapter contract test suite against a
  simulated Modbus device — no physical hardware required for CI, but the same
  adapter code would talk to real hardware unchanged.
- **Visible result**: the adapter contract test suite (from M1) passing against
  the real adapter, proving real and simulated adapters are behaviorally
  interchangeable from the controller's point of view.
- **Tests**: contract suite + adapter-specific tests (register mapping, error/
  timeout handling, reconnect behavior).
- **Exit criteria**: `site_controller.py` can be configured to use either adapter
  set via config, with zero code changes elsewhere.

### M9 — Dashboard integration & manual overrides
- By now the M4 dashboard's placeholders are all backed by real modules
  (protection state from M5, forecasts from M6, dispatch decisions from M7).
  This milestone finishes the dashboard rather than rebuilding it: wire the
  controls panel's manual overrides (e.g. force island, override a device
  setpoint) to actually issue commands, routed through the same protection
  gate as automated control — not just scenario-setup inputs anymore.
- Add a per-device real/simulated adapter selector, backed by M8.
- **Visible result**: issuing a manual override in the running dashboard
  demonstrably changes device behavior in the live scenario, and an override
  that would violate protection rules is visibly rejected.
- **Tests**: override commands are applied when safe and rejected when not,
  verified through the protection gate, not just the API layer; a smoke test
  driving a scenario and asserting the dashboard API reflects state changes
  (including override outcomes) as they happen.
- **Exit criteria**: a non-technical reviewer can open the dashboard, see the
  site's real state and decisions, and issue a manual override with a visible,
  correctly-gated effect.

### M10 — End-to-end validation & documentation
- Run and record the three headline scenarios: normal operation, outage/black-
  start/restoration, and forecast-driven dispatch savings.
- Update `README.md` (how to run the controller/dashboard/scenarios) and
  `CLAUDE.md` (new modules, how to extend adapters/forecasting/dispatch) per the
  standing convention.
- Write a short results summary (what the three scenarios demonstrate) as part of
  this milestone's own visible deliverable.
- **Exit criteria**: `docs/architecture.md` §6 Phase 1 is fully satisfied; Phase 2
  (multi-tenant hardening + sync layer) can start without revisiting any Phase 1
  interface.

## Out of scope for Phase 1 (deferred to later phases)

- Multi-tenant identity/capability-grant model, tenant isolation (Phase 2).
- Outbound-only broker comms, event-sourced cross-tier sync (Phase 2).
- Regional aggregation, grid-services/market interfaces, OT security hardening
  (Phases 2–4).
- ML-based forecasting, full MPC dispatch, broader protocol coverage beyond one
  Modbus/SunSpec profile (Phase 5).

## Risks / open questions

- **Optimization solver choice**: PuLP/LP assumes a linear cost model; if
  nonlinear battery degradation costs matter early, revisit before M7.
- **No physical hardware available yet**: M8's real adapter is validated only
  against `pymodbus`'s test server. Validating against an actual device should
  happen as soon as one is available, ideally without changing the adapter's
  public interface.
- **Dashboard scope creep**: M4 ships a full multi-device dashboard with replay
  earlier than a minimal plan would — keep it to "see state + charts + forecast
  + decisions + replay," and keep M9's overrides simple; a richer frontend is a
  Phase 3+ concern once regional dashboards are needed too.
- **Placeholder-to-real swaps (M4→M5/M6/M7)**: the M4 dashboard ships with
  inline placeholder forecast/decision logic so it has real data to chart
  before the real modules exist. Track these explicitly so none are
  accidentally left in place once M5–M7 land.
