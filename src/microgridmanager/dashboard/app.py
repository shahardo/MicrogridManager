"""M4 dashboard: a FastAPI service exposing live device status/charts and a
replay mode over completed telemetry runs, plus a minimal static web page.

This module is deliberately scenario-agnostic: `create_app` takes an
"engine" object (see `simulation.live_engine.SimulationEngine`'s docstring
for the small interface it must satisfy) and a `TelemetryStore`, and never
imports anything from `simulation/`. That keeps the installed package usable
against a real site controller later without changing this module — only
what's wired up in `simulation/dashboard_runner.py` (or a future production
entry point) needs to change.

M9 adds the manual-override endpoints
(`POST /api/controls/override/{battery,ev_charger,water_heater}`) and
`GET /api/devices` (the per-device real/simulated adapter-mode listing).
Both just forward to the engine — the actual protection-gated
apply-or-reject decision happens in `simulation.scenarios.household_day`,
not here, so this module's job stays routing HTTP to the engine's small
interface.

Post-M9 adds the simulation-disturbance endpoints (`POST
/api/controls/outage`, `POST /api/controls/disturbance/{load,pv,
demand_response}`) — same pattern, thin forwarding to the engine's
`trigger_outage`/`set_load_disturbance`/`set_pv_disturbance`/
`set_demand_response`, with the actual disturbance effect applied in
`simulation.scenarios.household_day.step_scenario`.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Protocol

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from microgridmanager.telemetry import TelemetryStore

STATIC_DIR = Path(__file__).parent / "static"
DEFAULT_TICK_INTERVAL_SECONDS = 1.0
DEFAULT_HISTORY_LIMIT = 200


class LiveEngine(Protocol):
    """The subset of `SimulationEngine` this module depends on."""

    running: bool
    speed: float
    grid_connected: bool
    run_id: str
    history: Any

    def start(self) -> None: ...
    def pause(self) -> None: ...
    def reset(self) -> None: ...
    def set_speed(self, speed: float) -> None: ...
    def set_grid_connected(self, connected: bool) -> None: ...
    def set_battery_override(self, power_w: float | None) -> None: ...
    def set_ev_override(self, power_w: float | None) -> None: ...
    def set_water_heater_override(self, force_heating: bool | None) -> None: ...
    def device_adapter_modes(self) -> list[dict[str, Any]]: ...
    def trigger_outage(self, duration_minutes: float | None) -> None: ...
    def set_load_disturbance(
        self, multiplier: float | None, duration_minutes: float | None
    ) -> None: ...
    def set_pv_disturbance(
        self, multiplier: float | None, duration_minutes: float | None
    ) -> None: ...
    def set_demand_response(
        self, max_import_w: float | None, duration_minutes: float | None
    ) -> None: ...
    def tick(self) -> dict: ...


class SpeedRequest(BaseModel):
    speed: float


class GridConnectionRequest(BaseModel):
    connected: bool


class PowerOverrideRequest(BaseModel):
    """`power_w=None` clears the override, reverting that device to
    automatic (dispatch/fallback-rule) control."""

    power_w: float | None = None


class WaterHeaterOverrideRequest(BaseModel):
    """`force_heating=None` clears the override, reverting to normal
    hysteresis control."""

    force_heating: bool | None = None


class OutageRequest(BaseModel):
    """`duration_minutes=None` (or omitted/non-positive) clears a pending
    outage and reconnects the grid immediately; otherwise the grid is
    disconnected for that many minutes and then automatically reconnects."""

    duration_minutes: float | None = None


class DisturbanceRequest(BaseModel):
    """`value=None` clears the disturbance. `duration_minutes=None` (or
    omitted/non-positive) leaves it active indefinitely, until explicitly
    cleared."""

    value: float | None = None
    duration_minutes: float | None = None


async def _run_engine_loop(engine: LiveEngine, base_interval_seconds: float) -> None:
    while True:
        if engine.running:
            engine.tick()
        await asyncio.sleep(base_interval_seconds / engine.speed)


def create_app(
    engine: LiveEngine,
    telemetry_store: TelemetryStore,
    *,
    tick_interval_seconds: float = DEFAULT_TICK_INTERVAL_SECONDS,
) -> FastAPI:
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        loop_task = asyncio.create_task(_run_engine_loop(engine, tick_interval_seconds))
        try:
            yield
        finally:
            loop_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await loop_task

    app = FastAPI(title="MicrogridManager Dashboard", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/state")
    def get_state() -> dict:
        latest = engine.history[-1] if engine.history else None
        return {
            "running": engine.running,
            "speed": engine.speed,
            "grid_connected": engine.grid_connected,
            "run_id": engine.run_id,
            "latest": latest,
        }

    @app.get("/api/history")
    def get_history(limit: int = DEFAULT_HISTORY_LIMIT) -> list[dict]:
        history = list(engine.history)
        return history[-limit:] if limit > 0 else history

    @app.post("/api/controls/start")
    def start() -> dict:
        engine.start()
        return {"running": engine.running}

    @app.post("/api/controls/pause")
    def pause() -> dict:
        engine.pause()
        return {"running": engine.running}

    @app.post("/api/controls/reset")
    def reset() -> dict:
        engine.reset()
        return {"running": engine.running, "run_id": engine.run_id}

    @app.post("/api/controls/speed")
    def set_speed(request: SpeedRequest) -> dict:
        try:
            engine.set_speed(request.speed)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"speed": engine.speed}

    @app.post("/api/controls/grid")
    def set_grid_connected(request: GridConnectionRequest) -> dict:
        engine.set_grid_connected(request.connected)
        return {"grid_connected": engine.grid_connected}

    @app.post("/api/controls/override/battery")
    def set_battery_override(request: PowerOverrideRequest) -> dict:
        engine.set_battery_override(request.power_w)
        return {"battery_power_w": request.power_w}

    @app.post("/api/controls/override/ev_charger")
    def set_ev_override(request: PowerOverrideRequest) -> dict:
        engine.set_ev_override(request.power_w)
        return {"ev_power_w": request.power_w}

    @app.post("/api/controls/override/water_heater")
    def set_water_heater_override(request: WaterHeaterOverrideRequest) -> dict:
        engine.set_water_heater_override(request.force_heating)
        return {"force_heating": request.force_heating}

    @app.get("/api/devices")
    def get_devices() -> list[dict]:
        return engine.device_adapter_modes()

    @app.post("/api/controls/outage")
    def trigger_outage(request: OutageRequest) -> dict:
        engine.trigger_outage(request.duration_minutes)
        return {"duration_minutes": request.duration_minutes}

    @app.post("/api/controls/disturbance/load")
    def set_load_disturbance(request: DisturbanceRequest) -> dict:
        engine.set_load_disturbance(request.value, request.duration_minutes)
        return {"multiplier": request.value, "duration_minutes": request.duration_minutes}

    @app.post("/api/controls/disturbance/pv")
    def set_pv_disturbance(request: DisturbanceRequest) -> dict:
        engine.set_pv_disturbance(request.value, request.duration_minutes)
        return {"multiplier": request.value, "duration_minutes": request.duration_minutes}

    @app.post("/api/controls/disturbance/demand_response")
    def set_demand_response(request: DisturbanceRequest) -> dict:
        engine.set_demand_response(request.value, request.duration_minutes)
        return {"max_import_w": request.value, "duration_minutes": request.duration_minutes}

    @app.get("/api/runs")
    def list_runs() -> list[str]:
        return telemetry_store.list_runs()

    @app.get("/api/runs/{run_id}/series")
    def list_series(run_id: str) -> list[str]:
        return telemetry_store.list_series(run_id)

    @app.get("/api/runs/{run_id}/data")
    def get_run_data(run_id: str) -> dict[str, list[dict]]:
        series_names = telemetry_store.list_series(run_id)
        if not series_names:
            raise HTTPException(status_code=404, detail=f"unknown run_id: {run_id!r}")
        return {
            series: [
                {"timestamp": sample.timestamp.isoformat(), "value": sample.value}
                for sample in telemetry_store.query(run_id=run_id, series=series)
            ]
            for series in series_names
        }

    return app
