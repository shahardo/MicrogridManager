"""The site protection & control state machine (M5): islanding detection,
black-start, and load-shedding priority tiers.

This is the safety-critical core described in the architecture doc — it must
be deterministic and independent of any network link (no dependency on a
regional/utility tier, no I/O), so it takes plain inputs each tick
(`grid_connected`, `available_power_w`, and each load's current demand) and
returns a decision. It knows nothing about specific asset types or protocols;
a scenario (or, later, a real site controller) is responsible for wiring its
own assets' demand into `SheddableLoad`s and applying the resulting
`ProtectionDecision.served` flags before any adapter's setpoint is written —
that call path is what makes this the "final gate" the architecture doc
requires: nothing should be able to write to an adapter without first passing
through here.

State machine::

    NORMAL --(grid lost)--> ISLANDING_TRANSITION --(1 tick)--> ISLANDED
    ISLANDED --(available < critical demand)--> BLACK_START
    BLACK_START --(available >= critical demand)--> ISLANDED
    ISLANDED / BLACK_START --(grid returns)--> RESTORATION --(1 tick)--> NORMAL

`ISLANDING_TRANSITION` and `RESTORATION` are one-tick transitional states —
they exist purely so the transition itself is visible in telemetry/logs
rather than an instantaneous, unobserved jump.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ProtectionState(str, Enum):
    NORMAL = "normal"
    ISLANDING_TRANSITION = "islanding_transition"
    ISLANDED = "islanded"
    BLACK_START = "black_start"
    RESTORATION = "restoration"


# A numeric encoding of ProtectionState, for contexts that can only carry
# numbers (the telemetry store's schema is float-valued, and chart series are
# numeric) — e.g. `row["protection_state"] = float(PROTECTION_STATE_CODES[state])`.
# Ordered roughly by "how far from normal operation" so a plotted line reads
# sensibly even before it's decoded back into a label.
PROTECTION_STATE_CODES: dict[ProtectionState, int] = {
    ProtectionState.NORMAL: 0,
    ProtectionState.ISLANDING_TRANSITION: 1,
    ProtectionState.ISLANDED: 2,
    ProtectionState.BLACK_START: 3,
    ProtectionState.RESTORATION: 4,
}


@dataclass(frozen=True)
class SheddableLoad:
    """One controllable load's current demand and shedding priority.

    `priority` is lower-is-more-critical: 0 is served first and shed last.
    The load(s) sharing the lowest priority number are what the state
    machine treats as "critical demand" when deciding whether the site needs
    to escalate to `BLACK_START`.
    """

    name: str
    priority: int
    demand_w: float


@dataclass(frozen=True)
class ProtectionDecision:
    """One tick's output: the (possibly just-transitioned) state, and which
    of the given loads may draw their full demand this tick."""

    state: ProtectionState
    grid_connected: bool
    available_power_w: float
    critical_demand_w: float
    served: dict[str, bool]

    def grid_exchange_allowed(self) -> bool:
        """Whether the site may import/export across the point of common
        coupling this tick. False while islanded/black-starting — the PCC is
        physically open, so nothing should cross it regardless of any
        residual power imbalance a scenario computes."""
        return self.state in (ProtectionState.NORMAL, ProtectionState.RESTORATION)


class ProtectionController:
    """Owns the current `ProtectionState` and decides, each tick, what state
    to be in and which loads to serve — see the module docstring for the
    full state diagram.

    `black_start_recovery_margin` (default 1.1, i.e. 10% headroom) is applied
    only to the BLACK_START -> ISLANDED transition: entering BLACK_START trips
    immediately at the exact shortfall (a protective relay trips fast), but
    leaving it requires available power to clear critical demand by this
    margin, not just barely equal it. Without this, a slowly-rising supply
    (e.g. PV ramping up at dawn) that hovers within a few watts of critical
    demand for several ticks makes the state flap between ISLANDED and
    BLACK_START every tick it crosses back and forth — the same chattering
    problem `SimulatedWaterHeaterAdapter` avoids with its own hysteresis.
    """

    def __init__(self, *, black_start_recovery_margin: float = 1.1) -> None:
        self._state = ProtectionState.NORMAL
        self._black_start_recovery_margin = black_start_recovery_margin

    @property
    def state(self) -> ProtectionState:
        return self._state

    def decide(
        self,
        *,
        grid_connected: bool,
        available_power_w: float,
        loads: list[SheddableLoad],
    ) -> ProtectionDecision:
        critical_demand_w = self._critical_demand_w(loads)
        self._state = self._next_state(
            grid_connected=grid_connected,
            available_power_w=available_power_w,
            critical_demand_w=critical_demand_w,
        )
        served = self._shed_decision(
            state=self._state, available_power_w=available_power_w, loads=loads
        )
        return ProtectionDecision(
            state=self._state,
            grid_connected=grid_connected,
            available_power_w=available_power_w,
            critical_demand_w=critical_demand_w,
            served=served,
        )

    def _next_state(
        self, *, grid_connected: bool, available_power_w: float, critical_demand_w: float
    ) -> ProtectionState:
        state = self._state

        if state == ProtectionState.NORMAL:
            if grid_connected:
                return ProtectionState.NORMAL
            return ProtectionState.ISLANDING_TRANSITION

        if state == ProtectionState.ISLANDING_TRANSITION:
            # A protective relay trips immediately on fault detection rather
            # than waiting to see if the fault clears, so a same-tick
            # recovery still completes the transition to ISLANDED first.
            return ProtectionState.NORMAL if grid_connected else ProtectionState.ISLANDED

        if state == ProtectionState.ISLANDED:
            if grid_connected:
                return ProtectionState.RESTORATION
            if available_power_w < critical_demand_w:
                return ProtectionState.BLACK_START
            return ProtectionState.ISLANDED

        if state == ProtectionState.BLACK_START:
            if grid_connected:
                return ProtectionState.RESTORATION
            if available_power_w >= critical_demand_w * self._black_start_recovery_margin:
                return ProtectionState.ISLANDED
            return ProtectionState.BLACK_START

        if state == ProtectionState.RESTORATION:
            return ProtectionState.NORMAL

        raise AssertionError(f"unhandled protection state: {state!r}")  # pragma: no cover

    @staticmethod
    def _critical_demand_w(loads: list[SheddableLoad]) -> float:
        if not loads:
            return 0.0
        highest_priority = min(load.priority for load in loads)
        return sum(load.demand_w for load in loads if load.priority == highest_priority)

    @staticmethod
    def _shed_decision(
        *, state: ProtectionState, available_power_w: float, loads: list[SheddableLoad]
    ) -> dict[str, bool]:
        if state in (
            ProtectionState.NORMAL,
            ProtectionState.ISLANDING_TRANSITION,
            ProtectionState.RESTORATION,
        ):
            # The grid backstops any shortfall (or absorbs any surplus), so
            # nothing needs shedding.
            return {load.name: True for load in loads}

        if state == ProtectionState.BLACK_START:
            # Nothing is re-energized until enough generation/storage
            # recovers to at least cover critical demand again (see
            # `_next_state`, which is what actually leaves this state).
            return {load.name: False for load in loads}

        # ISLANDED: serve loads most-critical-first while headroom lasts.
        served: dict[str, bool] = {}
        remaining_w = available_power_w
        for load in sorted(loads, key=lambda load: load.priority):
            if load.demand_w <= remaining_w:
                served[load.name] = True
                remaining_w -= load.demand_w
            else:
                served[load.name] = False
        return served
