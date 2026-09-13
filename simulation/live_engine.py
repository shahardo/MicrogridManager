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
"""

from __future__ import annotations

import itertools
from collections import deque
from datetime import datetime, timezone

from microgridmanager.dashboard.placeholders import persistence_forecast
from microgridmanager.telemetry import TelemetrySample, TelemetryStore
from simulation.scenarios import household_day

DEFAULT_STEP_SECONDS = 300.0
DEFAULT_HISTORY_LENGTH = 500


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
        self._run_counter = itertools.count(1)
        self.running = False
        self.speed = 1.0
        self.grid_connected = True
        self._reset_state()

    def _reset_state(self) -> None:
        self.assets = household_day.build_scenario(step_seconds=self._step_seconds)
        self.history: deque[dict] = deque(maxlen=self._history_length)
        self._last_row: dict | None = None
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

    def tick(self) -> dict:
        """Advance one control step and return this step's reading (the same
        row shape `household_day.step_scenario` produces — including its
        real `protection_state`/`*_served` fields — plus the M4 dashboard's
        remaining placeholder forecast/decision-variable fields)."""
        row = household_day.step_scenario(
            self.assets, self._step_seconds, grid_connected=self.grid_connected
        )

        row["pv_power_forecast_w"] = persistence_forecast(
            self._last_row, "pv_power_w", row["pv_power_w"]
        )
        row["household_load_power_forecast_w"] = persistence_forecast(
            self._last_row, "household_load_power_w", row["household_load_power_w"]
        )
        row["battery_soc_headroom"] = 1.0 - row["battery_soc"]
        row["charge_rule_output_w"] = row["battery_power_w"]

        projected_import_price, projected_export_price = self.assets.grid.peek_price(
            self.assets.clock.now + self.assets.clock.step
        )
        row["grid_projected_import_price_per_kwh"] = projected_import_price
        row["grid_projected_export_price_per_kwh"] = projected_export_price

        self._last_row = row
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
