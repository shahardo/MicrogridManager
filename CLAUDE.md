# CLAUDE.md

Guidance for Claude Code (or any agent) working in this repository.

## Project

MicrogridManager is a **Distributed Microgrid Management System (DMMS)**. See
[`docs/architecture.md`](docs/architecture.md) for the full conceptual design: a
three-tier (site → regional → utility) hierarchical architecture that scales from
a single microgrid to utility-wide orchestration of many microgrids, unifies real
hardware control and simulation behind one control interface, and assumes
multi-tenant ownership with intermittent connectivity between tiers.

Current status: Phase 1 implementation, M9 (dashboard integration & manual
overrides) complete, plus a post-M9 "simulation disturbance controls"
addition (see below); following the phased roadmap in `docs/architecture.md`
§6 and `docs/phase-1-dev-plan.md`, starting with a single-site controller +
simulation. **M8 (real Modbus/SunSpec adapters) was deliberately deferred**
and done *after* M9, not before it as the dev plan orders them — M9's
manual-override wiring doesn't need real adapters to exist, and its "per-
device real/simulated adapter selector" sub-bullet is scoped down
accordingly (see the `simulation/live_engine.py` and dashboard entries
below): the selector reports every device as `"simulated"`-only rather than
offering a real option that doesn't work yet. M8, whenever it lands, should
register a real adapter mode there rather than reopening this file's other
M9 changes.

**Post-M9: simulation disturbance controls.** Not a numbered milestone in
the dev plan — an ad hoc addition on top of M9's dashboard, adding a second
controls-panel mechanism (`ScenarioDisturbances`, alongside M9's
`ManualOverrides`) that perturbs the *scenario's own inputs* (household
demand, PV output, a grid-import cap) rather than a device's setpoint, so an
operator can watch protection/dispatch *react* to an outage, a demand swing,
cloud cover, or a utility demand-response event instead of dictating the
response directly. Deliberately does **not** include any wind-generation
control — this scenario has no wind turbine asset, only rooftop PV, and a
user request for one was explicitly declined in favor of dropping it
entirely rather than folding it into the PV/"clouds" control. See the
`household_day.py`, `live_engine.py`, and dashboard entries below for what
changed.

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
    yet — see the post-M9 `set_output_multiplier` addition below), `battery.py`
    (`PowerControllable` + `StateOfChargeReadable`;
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
  - `water_heater.py` also gained `set_shed(bool)` (M5): forces the heating
    element off regardless of hysteresis. It has no `PowerControllable`
    setpoint to override like the other loads, so M5's protection layer
    sheds it this way instead — the tank still depletes from draws either
    way, only reheating is disabled. **M9** adds a second, separate
    `set_manual_heating_override(bool | None)`: an operator-driven force-on/
    force-off, distinct from `set_shed` (a safety decision) — `set_shed`
    always wins over it inside `step()`; the calling scenario is
    responsible for only invoking the manual override when its own gating
    says that's safe (see `household_day._override_allowed` below).
  - `pv.py` also gained `set_output_multiplier(multiplier: float)`
    (post-M9): scales output relative to the irradiance profile's own curve
    (`1.0` = no effect), clamped to `[0.0, rated_power_w]` in `get_state()`.
    This is the dashboard's "clouds" disturbance control (e.g. `0.3` for
    heavy cloud cover) — deliberately *not* a `PowerControllable`
    curtailment mixin: it's a continuous scale on the profile-driven curve
    (and, unlike curtailment, can raise output above the curve too, not
    just cap it below), applied by `household_day.step_scenario` every tick
    from `ScenarioDisturbances.pv_multiplier` (see below).
- `src/microgridmanager/protection/state_machine.py` — **the M5 protection &
  control state machine**: `ProtectionState` (`NORMAL` ↔
  `ISLANDING_TRANSITION` ↔ `ISLANDED` ↔ `BLACK_START` ↔ `RESTORATION`,
  `ISLANDING_TRANSITION`/`RESTORATION` are one-tick transitional states so
  the transition itself is visible rather than instantaneous),
  `SheddableLoad` (name/priority/demand_w — priority 0 is most critical,
  served first, shed last), and `ProtectionController.decide(grid_connected,
  available_power_w, loads) -> ProtectionDecision` (state +
  `served: dict[name, bool]` + `grid_exchange_allowed()`). Deliberately takes
  plain inputs and does no I/O — deterministic and independent of any network
  link, per the architecture doc's safety invariant and this file's
  Working-conventions rule 5. `BLACK_START` requires exceeding critical
  demand by `black_start_recovery_margin` (default 10%) to recover to
  `ISLANDED`, not just barely clearing it — without that hysteresis (the same
  pattern `SimulatedWaterHeaterAdapter` uses), a slowly-rising supply
  hovering near the threshold for several ticks (e.g. PV ramping up at dawn)
  flaps between states every tick it crosses back and forth. `PROTECTION_STATE_CODES`
  maps each state to an int, since telemetry/chart series are numeric —
  `simulation/dashboard_runner`'s `static/app.js` decodes it back to a label
  client-side (`PROTECTION_STATE_LABELS`, kept in sync by hand).
  `tests/unit/protection/test_state_machine.py` covers every transition's
  guard condition and the priority-ordered shedding logic directly, with no
  scenario/adapter involved.
- `src/microgridmanager/forecasting/` — **M6's forecasting module**: the
  seam between historical series and a point forecast, the same shape as
  M1's `AssetAdapter` seam (`docs/phase-1-dev-plan.md`'s exit criteria: "M7
  can consume forecasts without knowing which model produced them").
  - `interface.py` — `HistoricalPoint` (timestamp/value) and the abstract
    `Forecaster.predict(history, target_time, *, fallback=0.0) -> float`.
    Implementations must ignore any `history` point at or after
    `target_time` — using it would be forecasting a value from itself —
    rather than relying on callers to pre-filter their own buffers.
  - `baseline.py` — two implementations: `PersistenceForecaster` (predicts
    `target_time` as whatever was most recently observed before it — this is
    the M4 dashboard's original inline forecast, now behind the real
    interface) and `SeasonalAverageForecaster` (the dev plan's "same-day-
    last-week average" option, generalized via `period`/`max_lookback_cycles`
    — `period=24h` predicts "same time of day, averaged over the last N
    days"; `period=7d` predicts "same day, averaged over the last N weeks").
    `SeasonalAverageForecaster` falls back to a plain persistence forecast
    while no cycle-old sample exists yet (e.g. a live session's first day),
    so early predictions degrade gracefully instead of returning `fallback`
    for a whole cycle.
  - `accuracy.py` — `mean_absolute_error()`/`mean_absolute_percentage_error()`
    over paired actual/forecast sequences (MAPE skips points where `actual`
    is exactly zero, e.g. PV power overnight, rather than dividing by zero).
  - `tests/unit/forecasting/` — `contracts.py` (`assert_forecast_contract`,
    mirroring the adapter suite's pattern: empty history returns `fallback`,
    predictions are deterministic, a constant history is predicted exactly,
    and points at/after `target_time` are ignored) and
    `test_contract_suite.py` parametrized over both baseline models — future
    ML-based models should extend this parametrization rather than get their
    own separate checks. `test_baseline.py` covers each model's specific
    behavior (persistence's most-recent-observation semantics; seasonal
    averaging's first-cycle fallback, same-time-of-day matching, and
    lookback-cycle limit). `test_accuracy.py` covers the metrics' known
    values plus the dev plan's "forecast accuracy within a defined bound on
    synthetic data with known patterns" exit criterion: on a synthetic
    repeating daily step pattern, `SeasonalAverageForecaster` stays under a
    fixed MAE bound and clearly beats `PersistenceForecaster` (whose error is
    concentrated at each day's step transitions).
  - `simulation/live_engine.py`'s `SimulationEngine` uses
    `SeasonalAverageForecaster` (period=24h) for the dashboard's live
    `pv_power_forecast_w`/`household_load_power_forecast_w` fields (see the
    `simulation/` entry below), and `simulation/forecast_report.py` is this
    milestone's CLI visible result — both replace the *callers* of the old
    placeholder without changing the dashboard's forecast panel, since the
    series names stay the same.
- `src/microgridmanager/dispatch/engine.py` — **M7's rolling-horizon economic
  dispatch engine**. `DispatchEngine` owns its own PV/load forecast history
  (two more `SeasonalAverageForecaster`s, same pattern as M6's — see its
  module docstring for why the water heater is deliberately *not* a third
  controlled asset here, just folded into the forecasted `load_actual_w`)
  and, each `dispatch()` call, forecasts a horizon (`horizon_hours`, default
  4.0 — chosen for LP solve speed; see the "M7 test performance" note below)
  and solves an LP (`pulp`, bundled CBC solver via `PULP_CBC_CMD`) that
  minimizes net grid cost by choosing the battery's charge/discharge and an
  optional EV charger's charging power, returning a `DispatchPlan` whose
  index-0 values (`battery_setpoint_w`/`ev_setpoint_w`) are this tick's
  decision. Two real bugs surfaced while building this, both about the LP
  having no reason to behave sensibly at its own edges — read
  `_solve`'s comments before changing the objective or the EV target
  handling:
  - **No terminal value** for energy left in the battery at the end of the
    horizon meant the LP would happily drain a fully-charged battery for a
    quick sale on the last step, since nothing beyond the horizon exists in
    its world. Fixed by crediting final battery energy at the horizon's
    average import price (`terminal_value` in `_solve`) — deliberately
    battery-only, not applied to the EV, since its target-SoC constraints
    already capture what charge it needs to keep.
  - **Infeasible EV targets** (a deadline that can't be reached at rated
    power in the time available) would otherwise make the *whole tick's*
    LP infeasible and raise `DispatchInfeasibleError`. `_clamped_ev_targets`
    proactively caps each target at what's actually achievable by that step,
    so an over-ambitious target still gets the best charge possible instead
    of failing the tick — see
    `tests/unit/dispatch/test_engine.py::test_unreachable_ev_target_is_clamped_instead_of_raising`.
  - **Post-M9: `max_import_w`** — an optional per-horizon-step cap on
    `dispatch()`'s grid-import variable, for the dashboard's demand-response
    disturbance control. Enforced as a tighter `upBound` on `grid_import[k]`
    (not a post-hoc clamp on the result) so the LP proactively shifts to
    battery/EV usage around the constrained window — the same "sees it
    coming" behavior M7's terminal-value fix already relies on.
    `_clamped_import_caps` floors an unreachably-low cap at the minimum
    import each step's forecasted load actually needs (`load - pv -
    battery.rated_power_w`), mirroring `_clamped_ev_targets`'s rationale —
    but unlike the EV clamp, this one **doesn't** account for the battery's
    remaining energy or the EV's own target-SoC constraints over the
    horizon, so an aggressive cap can still combine with those to leave the
    LP infeasible; `household_day.step_scenario`'s dispatch branch is what
    actually guarantees the tick never crashes (see below), catching
    `DispatchInfeasibleError` and retrying uncapped. See
    `tests/unit/dispatch/test_engine.py::test_max_import_w_caps_grid_import_across_the_horizon`
    and `::test_unreachable_import_cap_is_clamped_instead_of_raising`.

  `EVChargingState` is dispatch's own generic shape for "which horizon steps
  is a controllable EV plugged in, and what SoC must it reach by when" —
  `household_day._ev_dispatch_state()` is what translates a concrete
  `SimulatedEVChargerAdapter`'s known `sessions` (see the adapter's new
  `sessions` read-only property) into it, so the dispatch package itself
  never imports anything scenario- or adapter-specific.

  **M7 test performance**: solving an LP every tick adds real cost (~14ms at
  the 4h/48-step default horizon on this machine) that a 288-tick (24h,
  5-minute-step) batch run makes very noticeable — an early version of this
  milestone's tests took over a minute. `tests/scenarios/test_household_day.py`
  responds two ways: (1) every pre-existing M2/M4/M5 test now passes
  `dispatch_enabled=False`, since they check scenario physics/wiring that
  holds regardless of control strategy and dispatch has its own fast, fine-
  grained unit tests; (2) the dedicated dispatch/cost tests use a coarser
  10-minute step. Keep both practices for any new scenario test that doesn't
  specifically need dispatch turned on.
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
    `step_scenario()` returns one flat dict per tick (every device's
    power/status plus tariff price and, since M5, protection fields) that
    both `runner.py` (batch) and `live_engine.py` (live) feed into the
    telemetry store unchanged.
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
    - **M5**: wires the `protection/` module (above) to this scenario's
      three controllable loads — household load priority 0 (critical, shed
      last), water heater priority 1, EV charger priority 2 (most
      deferrable, shed first). Every tick, `step_scenario` first clears
      `household_load`'s override *before* reading its demand (a stale
      override from a previous shed would otherwise read back as zero
      demand, which — since household load is the only critical-priority
      load — silently zeroed `critical_demand_w` and made the state machine
      flap between `ISLANDED` and `BLACK_START` forever instead of ever
      recovering for real; read the comment above that line before changing
      this ordering), computes `available_power_w` as PV plus the battery's
      energy-limited (not just rated-power-limited — the same chattering
      trap) dischargeable power, gets a `ProtectionDecision`, and *then*
      applies it (zero override for unserved `PowerControllable` loads,
      `set_shed()` for the water heater) *before* stepping anything — that
      ordering is what makes this the "final gate" the architecture doc
      requires, not just a status readout. While
      `decision.grid_exchange_allowed()` is false, `grid.set_net_power_w(0.0)`
      is forced regardless of any residual imbalance — the PCC is physically
      open. `grid_outage_between(start, end)` builds a `grid_connected_at`
      callable for `run()` to script an outage (`runner.py`'s
      `--outage-start-hour`/`--outage-duration-hours` flags, household_day
      only, do this from the CLI). New row fields:
      `household_load_served`/`ev_charger_served`/`water_heater_served`
      (1.0/0.0) and `protection_state` (see `PROTECTION_STATE_CODES`). See
      `tests/scenarios/test_household_day.py::test_outage_triggers_islanding_black_start_and_restoration`
      (the M5 exit-criteria test — a scripted outage timed to end *before*
      the marginal dawn PV-vs-demand crossing, so black-start recovery is
      driven cleanly by the grid returning rather than a borderline
      crossing, produces exactly `NORMAL → ISLANDING_TRANSITION → ISLANDED →
      BLACK_START → RESTORATION → NORMAL`, zero grid power throughout, and
      every load shed at some point and restored by the end).
    - **M7**: replaces the fixed self-consumption rule with the real
      `microgridmanager.dispatch.DispatchEngine`, but only while
      `decision.grid_exchange_allowed()` (`dispatch_active` in the row) —
      islanded/black-starting has no grid to optimize against, so both
      battery and EV fall back to the pre-M7 rule/auto-charge exactly as
      before. `dispatch_enabled=False` (threaded through `build_scenario`/
      `step_scenario`/`run()`) reverts to that same fallback even while
      grid-connected — the "naive baseline"
      `simulation/dispatch_report.py` compares against. Water heater is
      stepped *before* the dispatch/fallback branch (reordered from the
      pre-M7 sequence) purely so its real post-step reading is available as
      part of the combined `load_actual_w` dispatch forecasts, since it's
      exogenous to dispatch either way. `_ev_dispatch_state()` builds the
      EV's per-horizon-step plugged-in/target-SoC input from its `sessions`
      property. New row fields: `dispatch_active` (1.0/0.0) and
      `dispatch_projected_cost_usd` (the LP's own horizon-cost estimate,
      surfaced for visibility, not used for control). **Sign convention
      note**: `plan.battery_setpoint_w` already matches
      `SimulatedBatteryAdapter`'s own convention (+ = discharge, - =
      charge) — apply it directly, don't renegate it (an earlier draft did
      and inverted every dispatch-driven battery decision).
    - **M9**: adds `ManualOverrides` (`battery_power_w`/`ev_power_w`/
      `water_heater_force_heating`, each `None` meaning "no override,
      automatic control decides") and `_override_allowed(decision,
      served_key=None)`, the gate every override is checked against —
      allowed only while `decision.grid_exchange_allowed()` (`NORMAL`/
      `RESTORATION`): while islanded/black-starting, the resilience-first
      fallback rule has full authority over the battery/EV/water heater to
      keep whatever critical loads it can powered, and a manual override
      must never be able to second-guess it, so this is the same "final
      gate" invariant M5 enforces against automated control, applied to
      manual control too. `served_key` additionally rejects an override for
      a load protection has actually shed (`None` for the battery, which
      isn't a `SheddableLoad`). Applied at the exact point each device's
      setpoint would otherwise be written — after dispatch/the fallback
      rule has already decided what it *would* do, so there's always a
      well-defined automatic value to fall back to when an override is
      rejected. New row fields (always present, so replay's
      position-for-position `reconstructRows` stays aligned even on a tick
      with no override pending):
      `battery_override_active`/`battery_override_power_w`/
      `battery_override_applied` and the same three fields for
      `ev_override_*`/`water_heater_override_*` (`water_heater_override_
      heating` instead of `_power_w`) — `*_active` is whether an override
      was requested this tick at all, `*_applied` is whether the gate let
      it through, so active-but-not-applied is a visibly rejected override
      rather than a silently dropped one. See
      `tests/scenarios/test_household_day.py::test_manual_overrides_are_applied_while_grid_connected`
      and `::test_manual_overrides_are_rejected_while_islanded`.
    - **Post-M9**: adds `ScenarioDisturbances` (`load_multiplier`,
      `pv_multiplier`, `max_import_w`, each paired with a `*_until` expiry)
      and `_disturbance_active(value, until, now)`. Distinct from
      `ManualOverrides` — a disturbance perturbs the scenario's own *inputs*
      rather than requesting a device setpoint, so automated control reacts
      to it (the point of the feature) instead of being bypassed. Applied at
      the top of `step_scenario`, before protection/dispatch ever see the
      affected values: `load_multiplier` scales `household_load`'s demand
      via its existing override mechanism (`set_active_power_w`, same one
      `overrides` uses) *before* the `SheddableLoad` list is built, so a
      scaled-up demand can genuinely trigger shedding; `pv_multiplier` is
      forwarded every tick to `SimulatedPVAdapter.set_output_multiplier`
      (always — active or not — so clearing/expiring restores `1.0` rather
      than leaving a stale multiplier the adapter has no way to know is
      gone); `max_import_w`, while dispatch is active, becomes a flat
      per-horizon-step cap passed to `DispatchEngine.dispatch()`'s
      `max_import_w` parameter (a deliberate simplification — only the
      immediate index-0 decision actually matters each tick) — wrapped in a
      `try`/`except DispatchInfeasibleError` that retries uncapped, since
      `_clamped_import_caps` alone can't rule out every way an aggressive
      cap combines with the battery's energy state or the EV's target-SoC
      constraints to leave no feasible plan (see the dispatch/engine.py
      entry above), and a disturbance must never crash the tick. New row
      fields (always present): `load_disturbance_active`/
      `load_disturbance_multiplier`, `pv_disturbance_active`/
      `pv_disturbance_multiplier`, `demand_response_active`/
      `demand_response_cap_w` — unlike `ManualOverrides`' `*_applied`
      fields, there's no applied-vs-rejected distinction here (protection
      still runs and may still shed loads in response, exactly like it
      would to any other real swing), so these just report whether the
      disturbance was requested and not yet expired. See
      `tests/scenarios/test_household_day.py::test_load_disturbance_scales_household_demand`,
      `::test_pv_disturbance_scales_pv_output`,
      `::test_disturbance_expires_after_its_duration`,
      `::test_demand_response_caps_grid_import`,
      `::test_demand_response_has_no_effect_while_islanded`, and
      `::test_demand_response_cap_never_crashes_dispatch_over_many_ticks`
      (the regression test for the infeasibility case above).
  - `runner.py` — CLI: `--scenario` (`normal_day` or `household_day`),
    `--step-seconds`, `--duration-hours`, `--output`, `--telemetry-db`,
    `--run-id`, and (household_day only, M5) `--outage-start-hour`/
    `--outage-duration-hours` to script a grid outage via
    `grid_outage_between`; writes the scenario's per-step readings to CSV
    (`output/<scenario>.csv` by default; `output/` is gitignored) **and**
    records every non-timestamp field of every row into the M3
    `TelemetryStore` under an auto-generated (or `--run-id`-supplied) run id
    (`output/telemetry.db` by default). `record_telemetry()` is the one place
    that translates a scenario's row-dict output into telemetry samples — the
    scenario/adapter code itself stays telemetry-agnostic.
  - `dispatch_report.py` — **M7's visible result**: CLI (`python -m
    simulation.dispatch_report`, `make dispatch-report`) running
    `household_day` twice (dispatch on vs. `dispatch_enabled=False`) and
    printing the total grid-cost difference — e.g. a 24h/300s run shows the
    dispatch-optimized day costing roughly half the naive rule's. Flags:
    `--step-seconds`, `--duration-hours`.
  - `live_engine.py` — **M4's `SimulationEngine`**: drives `household_day`
    one `tick()` at a time (rather than a fixed batch loop) so the dashboard
    can show it running live. Passes its `grid_connected` (the dashboard's
    manual toggle) straight into `step_scenario` (M5: this is now the real
    input to the protection state machine, not a placeholder) and adds the
    dashboard's remaining forecast/decision-variable fields to each tick's
    row: `pv_power_forecast_w`/`household_load_power_forecast_w` (since M6,
    a real `microgridmanager.forecasting.SeasonalAverageForecaster` per
    series — see below — replacing the M4 dashboard's inline
    `persistence_forecast` placeholder; `battery_soc_headroom` is a plain
    derived value and `charge_rule_output_w` mirrors `row["battery_power_w"]`
    — since M7 that's the real dispatch/fallback-rule decision, not a
    placeholder, kept under this name only because the dashboard's card
    already reads it) and `grid_projected_import/export_price_per_kwh` via
    `grid.peek_price()`)
    before recording to telemetry and appending to a rolling in-memory
    `history` deque the live charts read from. Maintains a small per-series
    `deque[HistoricalPoint]` (bounded to the forecaster's lookback window
    plus one cycle of margin) for `pv_power_w`/`household_load_power_w`,
    feeding each tick's forecast from history recorded strictly *before*
    that tick, then appending the actual observation afterwards — a
    forecaster must never see the value it's predicting. Exposes
    `start()`/`pause()`/`reset()`/`set_speed()`/`set_grid_connected()` for
    the controls panel. Deliberately lives under `simulation/` (not the
    installed package) since it wires up one specific scenario, mirroring
    `runner.py`'s role for batch runs — see its module docstring for the
    small duck-typed interface `microgridmanager.dashboard.app` depends on
    instead of importing this directly, so the installed dashboard package
    stays scenario-agnostic (a real site controller can be pointed at
    without changing it). **M9** adds `set_battery_override`/
    `set_ev_override`/`set_water_heater_override` (each just stashes the
    requested value on a `household_day.ManualOverrides` that `tick()`
    passes into `step_scenario` from then on — the actual apply-or-reject
    decision lives entirely in `household_day._override_allowed`, not here)
    and `device_adapter_modes()` (returns every device as `"simulated"`-only
    — see the "Current status" note at the top of this file for why the
    real option isn't backed yet). **Post-M9** adds the mirrored disturbance
    setters — `set_load_disturbance`/`set_pv_disturbance`/
    `set_demand_response(value, duration_minutes)`, each stashing a value +
    computed `*_until` expiry on `self._disturbances`, a
    `household_day.ScenarioDisturbances`, which `tick()` now also passes
    into `step_scenario` — and `trigger_outage(duration_minutes)`, which is
    the one disturbance that does *not* go through `ScenarioDisturbances`:
    it just reuses the existing `grid_connected` toggle and tracks its own
    expiry (`self._outage_until`), checked at the top of every `tick()`, so
    the grid reconnects automatically without introducing a third
    outage-scripting mechanism alongside `grid_connected_at`/
    `grid_outage_between` (both batch-only). Adds one dashboard-only row
    field, `outage_disturbance_active`, distinguishing a timed outage from
    the plain manual toggle staying off indefinitely (both already show up
    in `grid_connected`).
  - `dashboard_runner.py` — CLI (`python -m simulation.dashboard_runner`,
    `make run-dashboard`) wiring a `TelemetryStore` + `SimulationEngine` into
    `microgridmanager.dashboard.app.create_app()` and serving it with
    `uvicorn`. Flags: `--host`, `--port`, `--telemetry-db`, `--step-seconds`.
  - `forecast_report.py` — **M6's visible result**: CLI (`python -m
    simulation.forecast_report`, `make forecast-report`) that runs a
    scenario, replays both baseline `Forecaster`s (`PersistenceForecaster`,
    `SeasonalAverageForecaster`) online against its PV/load series exactly
    like `live_engine.tick()` does, and prints each model's MAE/MAPE — e.g.
    `make forecast-report ARGS="--scenario household_day --duration-hours
    72"` shows `SeasonalAverageForecaster` roughly a third of
    `PersistenceForecaster`'s error on both series once a full day of
    history exists. Flags: `--scenario` (`normal_day`/`household_day`),
    `--step-seconds`, `--duration-hours` (defaults to 72h so the seasonal
    model's daily-average behavior actually kicks in after the first day).
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
  `{connected}` (the manual grid connect/disconnect toggle — since M5, real
  input to the protection state machine, not a placeholder), `GET /api/runs`
  / `GET /api/runs/{run_id}/series` (thin wrappers over `TelemetryStore`),
  and `GET /api/runs/{run_id}/data` (every series for a run, bundled for
  replay). **M9** adds `POST /api/controls/override/{battery,ev_charger,
  water_heater}` (body `{power_w}` or `{force_heating}`; `null`/omitted
  clears the override) and `GET /api/devices` (the per-device adapter-mode
  listing from `engine.device_adapter_modes()`) — both just forward to the
  engine, keeping this module's job routing HTTP to its small interface
  rather than deciding anything itself. The M4 dashboard's `placeholders.py` (which held the inline
  `persistence_forecast()` stand-in) is gone as of M6 — `live_engine.py` now
  computes the forecast panel's fields from the real
  `microgridmanager.forecasting` module instead, with no change to this
  module or its panels, per the Phase 1 plan's tracked placeholder-to-real
  swaps. The decision-variables panel's last placeholder is gone as of M7
  too: its "Economic dispatch" card (`static/app.js`, replacing the old
  "Battery rule (placeholder, M7 replaces this)" card) shows
  `dispatch_active`, `battery_soc_headroom`, `charge_rule_output_w`, and
  `dispatch_projected_cost_usd` — all real fields from
  `household_day.step_scenario` now, with no dashboard/API changes needed
  beyond that one card's label and fields. `static/app.js` decodes the
  numeric `protection_state` field back to a label (`PROTECTION_STATE_LABELS`,
  mirrors `microgridmanager.protection.state_machine.PROTECTION_STATE_CODES`
  by hand — update both if the states ever change) for the "Protection state
  machine" decision card and the `chart-protection` chart (state plus each
  load's served/shed flag over time — M5's dashboard-visible result). Polls
  `/api/state` + `/api/history` every second in Live mode; Replay mode lists
  `/api/runs`, loads one run's
  `/api/runs/{id}/data`, reconstructs per-tick rows by indexing every
  series array position-for-position (`reconstructRows`), and scrubs through
  them client-side (play/pause/seek/speed) reusing the same card/chart
  renderers as the live view — proven to reproduce the live data exactly by
  `tests/unit/dashboard/test_replay.py`. **M9's** visible result:
  `static/index.html`/`app.js` add a "Manual overrides" controls-panel
  section (battery/EV power inputs + Set/Clear, a water-heater Auto/Force
  on/Force off select) that POSTs to the new endpoints, a matching "Manual
  overrides" decision card showing each device as `auto`, `<value> (applied)`,
  or `<value> (REJECTED by protection gate)` from that tick's
  `*_override_active`/`*_override_applied` fields, and a "Device adapters"
  panel rendering `GET /api/devices`' listing (a plain label per device
  today, not a working selector, since only `"simulated"` exists to select —
  see the "Current status" note at the top of this file). Also fixes the
  forecast chart's heading, which still said "placeholder persistence model,
  M6 replaces this" after M6 had already replaced it.

  **Post-M9** adds `POST /api/controls/outage` (body `{duration_minutes}`;
  `null`/omitted/non-positive clears a pending outage and reconnects
  immediately) and `POST /api/controls/disturbance/{load,pv,demand_response}`
  (body `{value, duration_minutes}`; `value=null` clears it, `duration_minutes
  =null`/omitted leaves it active indefinitely) — same thin-forwarding
  pattern as M9's override endpoints, extending the `LiveEngine` protocol
  with `trigger_outage`/`set_load_disturbance`/`set_pv_disturbance`/
  `set_demand_response`. Visible result: `static/index.html`/`app.js` add a
  "Simulation disturbances" controls-panel section (below "Manual
  overrides") — an outage-duration input + Trigger/Reconnect-now, and a
  multiplier/cap + duration input + Set/Clear row for each of household
  demand, PV/"clouds", and the demand-response cap — and a matching
  "Simulation disturbances" decision card (`describeDisturbance` in
  `app.js`, the disturbance-side counterpart to M9's `describeOverride`,
  simpler since there's no applied-vs-rejected distinction here) reading the
  new `*_disturbance_active`/`*_multiplier`/`demand_response_cap_w` row
  fields. See
  `tests/unit/dashboard/test_app.py::test_outage_control_disconnects_and_auto_reconnects`
  and the `test_*_disturbance_control_*` tests alongside it.
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
    target-SoC charging, water heater hysteresis and tank bounds — including
    M5's `set_shed()` forcing the element off and releasing back to normal
    hysteresis, and M9's `set_manual_heating_override()` forcing it on/off
    against hysteresis, `set_shed` winning over an active override, and
    clearing an override resuming hysteresis — grid cumulative import/export
    energy and tariff timing). `test_pv.py` also covers the post-M9
    `set_output_multiplier()` (scales output, clamps to rated power, and
    clearing it back to `1.0` restores the profile-driven curve exactly).
  - `tests/unit/protection/test_state_machine.py` — **M5's** direct unit
    tests for `ProtectionController`: every transition's guard condition
    (including same-tick grid recovery during `ISLANDING_TRANSITION`, the
    `black_start_recovery_margin` hysteresis, and the empty-loads edge case)
    and the priority-ordered shedding logic, with no scenario/adapter
    involved.
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
    balance) — plus the M5 outage/black-start/restoration exit-criteria test,
    the M7 dispatch exit-criteria tests, and the M9 manual-override
    exit-criteria tests (applied while grid-connected; visibly rejected —
    active but not applied — for every tick of a scripted outage, back to
    applied once `NORMAL` resumes) (see the `household_day.py` entry above),
    plus the post-M9 disturbance tests (load/PV multipliers scale demand/PV
    relative to the same tick with no disturbance, a `*_until` expiry
    actually expires, a demand-response cap binds grid import while dispatch
    runs and is inert-but-still-reported-active while islanded, and the
    infeasibility-fallback regression test that ticks 60 times with an
    aggressive, indefinite import cap active to prove `step_scenario` never
    lets `DispatchInfeasibleError` propagate).
    Every M2/M4/M5/M9/post-M9 test here passes `dispatch_enabled=False`
    unless it specifically needs dispatch, and the M7-specific tests use a
    coarser step — see this file's own module docstring and the "M7 test
    performance" note under `dispatch/engine.py` above before adding a new
    scenario test.
  - `tests/unit/dispatch/test_engine.py` — **M7's** direct unit tests for
    `DispatchEngine`/`_solve`: plan shape, every physical bound (rated
    power, SoC), the terminal-value fix (charges from surplus even on a
    flat tariff, prefers discharging into a visible price spike over an
    equally-flat cheap period), the EV target-SoC-by-deadline constraint,
    plugged-in-window bounds, the target-clamping fix, that forecasts
    never see the current tick's own actual, and (post-M9) the
    `max_import_w` demand-response cap binding grid import across the
    horizon and its own clamping fix for an unreachably-low cap — all with
    no scenario/adapter involved, so they stay fast regardless of how
    expensive a full scenario run with dispatch enabled is.
  - `tests/unit/dashboard/` — `test_app.py` drives the FastAPI app's API
    (state/history/controls/runs/series) via `TestClient`, ticking the engine
    manually rather than relying on the real-time background loop so tests
    stay deterministic — including M9's override endpoints (each control
    updates the engine and the next tick's row reflects it, including a
    grid-disconnected case proving an override is actually rejected by the
    protection gate, not just accepted by the API layer), `/api/devices`,
    and the post-M9 disturbance endpoints (outage trigger + auto-reconnect
    across multiple manual ticks, and each of the load/PV/demand-response
    disturbance endpoints updating the engine and showing up in the next
    tick's row); `test_replay.py` is the M4 replay exit-criteria test — it
    ticks a
    `SimulationEngine` directly, captures the exact rows returned, then
    asserts `GET /api/runs/{run_id}/data` reconstructs those same rows
    value-for-value and timestamp-for-timestamp.
- `pyproject.toml` — project config; dependencies managed with `uv`
  (`uv sync --group dev` creates `.venv` and installs everything — never
  install packages globally). No new runtime dependencies were added through
  M3 (kept dependency-light per the project's zero-ops bias — CSV output and
  the telemetry store use only the standard library, `csv` and `sqlite3`).
  M4 adds `fastapi` and `uvicorn[standard]` as runtime dependencies (per the
  Phase 1 tech stack table) and `httpx` to the `dev` group (required by
  FastAPI's `TestClient`). M5 adds none (the state machine is pure Python
  logic, no I/O). M6 adds none either (the forecasting module is pure Python
  logic over plain in-memory sequences, no I/O). M7 adds `pulp` (LP
  modeling + a bundled CBC solver via `PULP_CBC_CMD`) — per the Phase 1 tech
  stack table's "PuLP (LP) to start" choice.
- `Makefile` — `make test` (pytest), `make lint` (ruff), `make run-scenario`
  (runs the M2 normal-day scenario by default; pass `ARGS="--scenario
  household_day"` for M4's six-device scenario), `make query-telemetry
  ARGS="..."` (M3's CLI), `make run-dashboard` (M4's dashboard — starts a
  uvicorn server at `http://127.0.0.1:8000`), `make forecast-report
  ARGS="..."` (M6's CLI — see `simulation/forecast_report.py` above),
  `make dispatch-report ARGS="..."` (M7's CLI — see
  `simulation/dispatch_report.py` above). Native Windows PowerShell usually
  does not ship with `make`, so Windows users should run the equivalent
  `uv run ...` commands directly instead of `make`.
