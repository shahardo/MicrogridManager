"""Simulated EV charger adapter (M4): a session-based controllable load.

The car plugs in and out on a schedule of `EVSession`s. While plugged in and
below its session's target state of charge, the charger draws power
automatically at its rated power (an "uncontrolled" charge strategy — there is
no dispatch engine yet, per M7): `step()` decides and integrates this every
tick, exactly like `SimulatedBatteryAdapter.step()` clamps a setpoint to the
power actually applied. `set_active_power_w` records an override setpoint
immediately (so it round-trips through `get_state()` without needing a
`step()`, matching the shared adapter contract) that `step()` then applies
instead of its own auto-charge decision — mirroring `SimulatedLoadAdapter`'s
override pattern, so a future dispatcher (M7) can shape the charge curve
without this adapter changing.

Each time the car goes from plugged-in to unplugged, `step()` also deducts
`commute_energy_wh` (default 0, i.e. no effect) from the battery — modeling
the energy used driving away. Without this, a car charged to its session
target has nothing left to charge on any later day once replugged, which
made a recurring daily schedule (see `simulation/scenarios/household_day.py`)
still look flat after the first charge — plugged in and unplugged on
schedule, but never drawing power again.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from microgridmanager.adapters.interface import (
    AssetAdapter,
    AssetType,
    EVChargerState,
    PowerControllable,
    StateOfChargeReadable,
)

from .clock import SimulationClock


@dataclass(frozen=True)
class EVSession:
    """One plug-in session: the car is plugged in from `plug_in` until
    `deadline`, and should reach `target_soc` by then."""

    plug_in: datetime
    deadline: datetime
    target_soc: float = 1.0


class SimulatedEVChargerAdapter(AssetAdapter, PowerControllable, StateOfChargeReadable):
    def __init__(
        self,
        asset_id: str,
        *,
        clock: SimulationClock | None = None,
        capacity_wh: float = 60_000.0,
        rated_power_w: float = 7_200.0,
        initial_soc: float = 0.4,
        sessions: list[EVSession] | None = None,
        commute_energy_wh: float = 0.0,
    ) -> None:
        if not 0.0 <= initial_soc <= 1.0:
            raise ValueError("initial_soc must be within [0.0, 1.0]")
        self._asset_id = asset_id
        self._clock = clock or SimulationClock()
        self._capacity_wh = capacity_wh
        self._rated_power_w = rated_power_w
        self._energy_wh = capacity_wh * initial_soc
        self._sessions = sorted(sessions or [], key=lambda s: s.plug_in)
        self._commute_energy_wh = commute_energy_wh
        self._was_plugged_in = self._active_session() is not None
        self._override_w: float | None = None
        self._active_power_w = 0.0

    @property
    def asset_id(self) -> str:
        return self._asset_id

    @property
    def asset_type(self) -> AssetType:
        return AssetType.EV_CHARGER

    @property
    def sessions(self) -> tuple[EVSession, ...]:
        """Read-only view of this charger's known plug-in schedule (sorted by
        `plug_in`), so a dispatch engine (M7) can see plugged-in windows and
        deadlines over its planning horizon — sessions are deterministic and
        known in advance here, the same way a real system would know a
        charging deadline from the vehicle or a scheduling app."""
        return tuple(self._sessions)

    def set_active_power_w(self, watts: float) -> None:
        self._override_w = watts
        self._active_power_w = watts

    def clear_override(self) -> None:
        self._override_w = None

    def get_state_of_charge(self) -> float:
        return self._energy_wh / self._capacity_wh

    def _active_session(self) -> EVSession | None:
        now = self._clock.now
        for session in self._sessions:
            if session.plug_in <= now < session.deadline:
                return session
        return None

    def _auto_power_w(self, session: EVSession | None) -> float:
        if session is None:
            return 0.0
        return self._rated_power_w if self.get_state_of_charge() < session.target_soc else 0.0

    def get_state(self) -> EVChargerState:
        session = self._active_session()
        plugged_in = session is not None
        soc = self.get_state_of_charge()
        time_remaining_s = (
            (session.deadline - self._clock.now).total_seconds() if plugged_in else None
        )

        return EVChargerState(
            asset_id=self._asset_id,
            asset_type=self.asset_type,
            timestamp=self._clock.now,
            online=True,
            active_power_w=self._active_power_w,
            plugged_in=plugged_in,
            charging=self._active_power_w > 0.0,
            state_of_charge=soc,
            target_state_of_charge=session.target_soc if plugged_in else soc,
            time_remaining_s=time_remaining_s,
        )

    def step(self, dt_seconds: float) -> None:
        """Decide this tick's charging power (the override if one is set,
        otherwise the auto-charge rule) and integrate state of charge over
        `dt_seconds`, clamping the applied power to what's actually
        chargeable — a car can't charge past 100% or discharge (no V2G).

        Also detects the plugged-in -> unplugged transition and, if so,
        deducts `commute_energy_wh` once for that trip."""
        session = self._active_session()
        plugged_in = session is not None
        if self._was_plugged_in and not plugged_in and self._commute_energy_wh > 0.0:
            self._energy_wh = max(0.0, self._energy_wh - self._commute_energy_wh)
        self._was_plugged_in = plugged_in

        if self._override_w is None:
            self._active_power_w = self._auto_power_w(session)

        dt_hours = dt_seconds / 3600.0
        requested_w = max(0.0, self._active_power_w)

        if dt_hours <= 0.0 or requested_w <= 0.0:
            self._active_power_w = 0.0
            return

        max_chargeable_wh = self._capacity_wh - self._energy_wh
        energy_in_wh = max(0.0, min(requested_w * dt_hours, max_chargeable_wh))
        self._energy_wh = max(0.0, min(self._capacity_wh, self._energy_wh + energy_in_wh))
        self._active_power_w = energy_in_wh / dt_hours
