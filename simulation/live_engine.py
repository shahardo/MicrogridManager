"""M4 live simulation engine: drives the `household_day` scenario one tick at
a time, on demand, so the dashboard can show it running live (start/pause/
reset/speed) instead of only replaying a completed batch run.

This lives under `simulation/` (not the installed package) because it wires
up a specific scenario, matching `runner.py`'s role for batch runs.
`microgridmanager.dashboard.app` only depends on this engine's small
duck-typed interface (`running`/`speed`/`grid_connected`/`run_id`/`history`
plus `start()`/`pause()`/`reset()`/`set_speed()`/`set_grid_connected()`/
`tick()`), never the other way around, so the installed dashboard package
stays scenario-agnostic and could later be pointed at a real site controller
instead of a simulated one without changing.

Every tick is recorded to the telemetry store under this engine's current
`run_id` (so a live session can later be replayed exactly like any other
recorded run) and kept in a rolling in-memory `history` buffer the live
charts read from without hitting the database each poll.

Since M6, the PV/load forecast fields are produced by the real
`microgridmanager.forecasting` module (a `SeasonalAverageForecaster` per
series) instead of the M4 dashboard's inline `persistence_forecast`
placeholder — the dashboard's forecast panel and chart read the same
`pv_power_forecast_w`/`household_load_power_forecast_w` fields either way, so
this swap needed no panel changes.

M9 adds the controls panel's manual override setters
(`set_battery_override`/`set_ev_override`/`set_water_heater_override`): each
just stashes the requested value on `self._overrides`, a
`household_day.ManualOverrides`, which `tick()` passes into `step_scenario`
every tick from then on — the actual safety gating (whether an override is
allowed to take effect this tick) lives entirely in `household_day`'s
`_override_allowed`, not here, so this engine stays a thin driver. It also
adds `device_adapter_modes()`: every device is `"simulated"`-only until M8
lands a real Modbus/SunSpec adapter and registers it as a second available
mode here — this method is the seam M8 extends, not a working real/simulated
switch on its own yet.

Post-M9 adds the "simulation disturbances" controls panel: `set_load_
disturbance`/`set_pv_disturbance`/`set_demand_response`, each stashing a
value + expiry on `self._disturbances`, a `household_day.
ScenarioDisturbances`, mirroring the overrides pattern above — the actual
effect (perturbing demand/PV/the grid-import cap) lives in `household_day.
step_scenario`, not here. `trigger_outage(duration_minutes)` is the one
disturbance that does *not* go through `ScenarioDisturbances`: it just
reuses the existing `grid_connected` toggle (the same one the M4 controls
panel already drives) and this engine tracks its own expiry
(`self._outage_until`), checked at the top of every `tick()`, so the grid
comes back automatically without a live operator having to remember to
re-toggle it — a third mechanism alongside `grid_connected_at` (batch) and
`grid_outage_between` (batch) would be redundant, so this is deliberately
just a timed convenience over the same boolean.
"""

from __future__ import annotations

import itertools
from collections import deque
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from microgridmanager.forecasting import HistoricalPoint, SeasonalAverageForecaster
from microgridmanager.telemetry import TelemetrySample, TelemetryStore
from simulation.scenarios import household_day

DEFAULT_STEP_SECONDS = 300.0
DEFAULT_HISTORY_LENGTH = 500

# Every controllable device this scenario wires up, for `device_adapter_modes`
# (M9) — mirrors `HouseholdScenarioAssets`' fields, minus the non-device
# `clock`/`protection`/`dispatch` entries.
DEVICE_IDS = ("pv", "battery", "household_load", "ev_charger", "water_heater", "grid")

# One forecaster per series: "same time of day, averaged over the last week"
# — a meaningfully better baseline than persistence for this scenario's daily-
# repeating household/PV patterns (see forecasting/baseline.py), while still
# degrading gracefully to a persistence-style forecast during a run's first
# day, before any seasonal history exists yet.
FORECAST_PERIOD = timedelta(hours=24)
FORECAST_MAX_LOOKBACK_CYCLES = 7


class SimulationEngine:
    def __init__(
        self,
        telemetry_store: TelemetryStore,
        *,
        step_seconds: float = DEFAULT_STEP_SECONDS,
        history_length: int = DEFAULT_HISTORY_LENGTH,
    ) -> None:
        self._telemetry_store = telemetry_store
        self._step_seconds = step_seconds
        self._history_length = history_length
        # Enough ticks to cover the forecaster's full lookback window, plus
        # one cycle of margin so the oldest cycle it still needs is never
        # evicted mid-tick.
        self._forecast_history_length = int(
            FORECAST_PERIOD.total_seconds()
            / self._step_seconds
            * (FORECAST_MAX_LOOKBACK_CYCLES + 1)
        )
        self._pv_forecaster = SeasonalAverageForecaster(
            period=FORECAST_PERIOD, max_lookback_cycles=FORECAST_MAX_LOOKBACK_CYCLES
        )
        self._load_forecaster = SeasonalAverageForecaster(
            period=FORECAST_PERIOD, max_lookback_cycles=FORECAST_MAX_LOOKBACK_CYCLES
        )
        self._run_counter = itertools.count(1)
        self.running = False
        self.speed = 1.0
        self.grid_connected = True
        self._reset_state()

    def _reset_state(self) -> None:
        self.assets = household_day.build_scenario(step_seconds=self._step_seconds)
        self.history: deque[dict] = deque(maxlen=self._history_length)
        self._pv_history: deque[HistoricalPoint] = deque(maxlen=self._forecast_history_length)
        self._load_history: deque[HistoricalPoint] = deque(maxlen=self._forecast_history_length)
        self._overrides = household_day.ManualOverrides()
        self._disturbances = household_day.ScenarioDisturbances()
        self._outage_until: datetime | None = None
        run_number = next(self._run_counter)
        started_at = datetime.now(timezone.utc)
        self.run_id = f"live-{run_number}-{started_at:%Y%m%dT%H%M%SZ}"

    def start(self) -> None:
        self.running = True

    def pause(self) -> None:
        self.running = False

    def reset(self) -> None:
        self.running = False
        self._reset_state()

    def set_speed(self, speed: float) -> None:
        if speed <= 0:
            raise ValueError("speed must be positive")
        self.speed = speed

    def set_grid_connected(self, connected: bool) -> None:
        """Manual grid connect/disconnect toggle (M4 controls panel). Since
        M5, this is the real input signal the protection state machine
        consumes each tick — toggling it off actually islands the site,
        sheds loads by priority, and can drive a black start."""
        self.grid_connected = connected

    def set_battery_override(self, power_w: float | None) -> None:
        """M9 manual override: request the battery hold `power_w` (+ =
        discharge, - = charge) instead of whatever dispatch/the fallback
        rule would choose. `None` clears it, reverting to automatic control.
        Sticky across ticks until cleared or replaced; whether it's actually
        applied each tick is decided by `household_day`'s protection gate
        and reported back in that tick's row (`battery_override_applied`)."""
        self._overrides = replace(self._overrides, battery_power_w=power_w)

    def set_ev_override(self, power_w: float | None) -> None:
        """M9 manual override: request the EV charger draw `power_w` instead
        of dispatch/the auto-charge rule. `None` clears it. See
        `set_battery_override` for the applied-vs-rejected reporting."""
        self._overrides = replace(self._overrides, ev_power_w=power_w)

    def set_water_heater_override(self, force_heating: bool | None) -> None:
        """M9 manual override: force the water heater's element on (`True`)
        or off (`False`) regardless of hysteresis, or clear the override
        (`None`) to release it back to automatic control. See
        `set_battery_override` for the applied-vs-rejected reporting."""
        self._overrides = replace(self._overrides, water_heater_force_heating=force_heating)

    def trigger_outage(self, duration_minutes: float | None) -> None:
        """Simulation disturbance: force the grid disconnected for
        `duration_minutes` from now, then automatically reconnect. `None`
        (or a non-positive duration) clears a pending outage and reconnects
        immediately — this is the one manual override the operator still has
        on top of it: toggling `grid_connected` directly (the M4/M5 control)
        at any time overrides whatever this method last set, and a
        currently-running timed outage doesn't fight back."""
        if duration_minutes is None or duration_minutes <= 0:
            self._outage_until = None
            self.grid_connected = True
            return
        self._outage_until = self.assets.clock.now + timedelta(minutes=duration_minutes)
        self.grid_connected = False

    def set_load_disturbance(
        self, multiplier: float | None, duration_minutes: float | None
    ) -> None:
        """Simulation disturbance: scale household demand by `multiplier`
        (e.g. 1.5 for "high demand", 0.5 for "low demand") for
        `duration_minutes`, or indefinitely if `None`. `multiplier=None`
        clears it. See `household_day.ScenarioDisturbances`."""
        until = self._disturbance_until(duration_minutes) if multiplier is not None else None
        self._disturbances = replace(
            self._disturbances, load_multiplier=multiplier, load_multiplier_until=until
        )

    def set_pv_disturbance(self, multiplier: float | None, duration_minutes: float | None) -> None:
        """Simulation disturbance ("clouds"): scale PV output by
        `multiplier` (e.g. 0.2 for heavy cloud cover) for
        `duration_minutes`, or indefinitely if `None`. `multiplier=None`
        clears it. See `household_day.ScenarioDisturbances`."""
        until = self._disturbance_until(duration_minutes) if multiplier is not None else None
        self._disturbances = replace(
            self._disturbances, pv_multiplier=multiplier, pv_multiplier_until=until
        )

    def set_demand_response(
        self, max_import_w: float | None, duration_minutes: float | None
    ) -> None:
        """Simulation disturbance: cap grid import at `max_import_w` (a
        utility demand-response event) for `duration_minutes`, or
        indefinitely if `None`. `max_import_w=None` clears it. Only takes
        effect while dispatch is actively running (grid-connected, not
        islanded/black-starting, `dispatch_enabled`) — see
        `household_day.ScenarioDisturbances`."""
        until = self._disturbance_until(duration_minutes) if max_import_w is not None else None
        self._disturbances = replace(
            self._disturbances, max_import_w=max_import_w, max_import_w_until=until
        )

    def _disturbance_until(self, duration_minutes: float | None) -> datetime | None:
        if duration_minutes is None or duration_minutes <= 0:
            return None
        return self.assets.clock.now + timedelta(minutes=duration_minutes)

    def device_adapter_modes(self) -> list[dict[str, object]]:
        """Per-device real/simulated adapter selector (M9): every device is
        `"simulated"`-only until M8 lands a real adapter and registers it as
        a second available mode here — see this module's docstring."""
        return [
            {"device": device_id, "adapter_mode": "simulated", "available_modes": ["simulated"]}
            for device_id in DEVICE_IDS
        ]

    def tick(self) -> dict:
        """Advance one control step and return this step's reading (the same
        row shape `household_day.step_scenario` produces — including its
        real `protection_state`/`*_served`/`dispatch_active`/
        `dispatch_projected_cost_usd` fields — plus a couple of small
        dashboard-only derived/duplicate fields added below)."""
        if self._outage_until is not None and self.assets.clock.now >= self._outage_until:
            self._outage_until = None
            self.grid_connected = True

        row = household_day.step_scenario(
            self.assets,
            self._step_seconds,
            grid_connected=self.grid_connected,
            overrides=self._overrides,
            disturbances=self._disturbances,
        )
        # Dashboard-only: whether the current disconnect is a *timed*
        # disturbance (`trigger_outage`) as opposed to the plain manual
        # grid-connect toggle staying off indefinitely — both already show up
        # in `grid_connected`, this just distinguishes why.
        row["outage_disturbance_active"] = float(self._outage_until is not None)
        timestamp = datetime.fromisoformat(row["timestamp"])

        # Forecast this tick from history recorded *before* it, then only
        # append the actual observation afterwards — a forecaster must never
        # see the value it's predicting.
        row["pv_power_forecast_w"] = self._pv_forecaster.predict(
            self._pv_history, timestamp, fallback=row["pv_power_w"]
        )
        row["household_load_power_forecast_w"] = self._load_forecaster.predict(
            self._load_history, timestamp, fallback=row["household_load_power_w"]
        )
        self._pv_history.append(HistoricalPoint(timestamp=timestamp, value=row["pv_power_w"]))
        self._load_history.append(
            HistoricalPoint(timestamp=timestamp, value=row["household_load_power_w"])
        )

        row["battery_soc_headroom"] = 1.0 - row["battery_soc"]
        # As of M7 this mirrors the real dispatch/self-consumption-rule
        # decision (row["battery_power_w"]) rather than a placeholder — kept
        # as its own field since the dashboard's decision-variables card
        # already reads it under this name.
        row["charge_rule_output_w"] = row["battery_power_w"]

        projected_import_price, projected_export_price = self.assets.grid.peek_price(
            self.assets.clock.now + self.assets.clock.step
        )
        row["grid_projected_import_price_per_kwh"] = projected_import_price
        row["grid_projected_export_price_per_kwh"] = projected_export_price

        self.history.append(row)
        self._record_telemetry(row)
        self.assets.clock.tick()
        return row

    def _record_telemetry(self, row: dict) -> None:
        timestamp = datetime.fromisoformat(row["timestamp"])
        samples = [
            TelemetrySample(run_id=self.run_id, timestamp=timestamp, series=key, value=value)
            for key, value in row.items()
            if key != "timestamp"
        ]
        self._telemetry_store.record_many(samples)
