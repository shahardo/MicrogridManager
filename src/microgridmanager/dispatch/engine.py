"""M7 rolling-horizon economic dispatch engine.

Reuses the M6 forecasting seam (`microgridmanager.forecasting`) exactly the
way its own docstring predicted this milestone would: this engine consumes
`Forecaster.predict()` output without knowing or caring which model produced
it. It owns rolling history for two exogenous series (site PV output and
"everything else that must just be served" — household load plus any other
uncontrolled demand a caller lumps in) and, each tick, forecasts them over a
horizon and solves a linear program that minimizes net grid cost by choosing:

- the battery's charge/discharge schedule, and
- (optionally) a controllable EV charger's charging schedule, subject to
  reaching a target state of charge by a known deadline.

Everything else a site might have (e.g. a water heater) is treated as
exogenous, forecasted, must-serve demand — folded into the caller-supplied
`load_actual_w` — not shaped by dispatch. That's a deliberate scope cut, not
an oversight: giving dispatch a lever over *every* asset is possible (the
architecture doc's asset/adapter interface would support it) but the
marginal value of controlling a single more-deferrable-but-smaller load is
much lower than the battery/EV, so it's left as a documented extension.

The LP is a standard economic-dispatch formulation: split battery/EV power
into non-negative charge/discharge variables (so simultaneous charge+
discharge is never optimal whenever import price >= export price, which
holds for every tariff this project uses, so no binary variables — and thus
no MILP — are needed to forbid it), track energy state as a running sum
across horizon steps, and bound it to each asset's capacity. This mirrors
`SimulatedBatteryAdapter`/`SimulatedEVChargerAdapter`'s own physics
(charging efficiency applied on the way in, discharge lossless at the
terminals, no V2G) so a dispatch plan applied to those adapters plays out
close to what the LP predicted.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import pulp

from microgridmanager.forecasting import HistoricalPoint, SeasonalAverageForecaster

DEFAULT_HORIZON_HOURS = 4.0
DEFAULT_FORECAST_PERIOD = timedelta(hours=24)
DEFAULT_FORECAST_MAX_LOOKBACK_CYCLES = 7


class DispatchInfeasibleError(RuntimeError):
    """Raised when the LP has no feasible solution at all — should be rare;
    `DispatchEngine` proactively clamps the one input known to be able to
    cause this (an EV target state of charge that can't be reached by its
    deadline given rated power and time remaining) before it ever reaches
    the solver, precisely so this stays rare."""


@dataclass(frozen=True)
class BatteryState:
    state_of_charge: float
    capacity_wh: float
    rated_power_w: float
    round_trip_efficiency: float = 0.95


@dataclass(frozen=True)
class EVChargingState:
    """The EV's current state plus its known plug-in windows and any energy
    target over the dispatch horizon. Sessions are deterministic/known in
    advance (not forecasted) — a real system would know a session's deadline
    from the vehicle or a scheduling app, the same information this project's
    `EVSession` already carries.

    `plugged_in` and `target_soc_by_step` are both indexed by horizon step
    (0 = the first step *after* now); `target_soc_by_step[k] = s` means "by
    the end of step k, state of charge must be at least s".
    """

    state_of_charge: float
    capacity_wh: float
    rated_power_w: float
    plugged_in: list[bool]
    target_soc_by_step: dict[int, float] = field(default_factory=dict)


@dataclass(frozen=True)
class DispatchPlan:
    """This tick's decision (index 0 of each list) plus the full horizon."""

    battery_power_w: list[float]  # + = discharge, - = charge (matches adapter convention)
    ev_power_w: list[float] | None
    pv_forecast_w: list[float]
    load_forecast_w: list[float]
    projected_cost: float

    @property
    def battery_setpoint_w(self) -> float:
        return self.battery_power_w[0]

    @property
    def ev_setpoint_w(self) -> float | None:
        return self.ev_power_w[0] if self.ev_power_w else None


class DispatchEngine:
    """Owns forecast history for PV/load and solves one rolling-horizon
    economic dispatch per call to `dispatch()`. Stateful (the forecast
    history) but takes plain inputs and returns a plain plan — nothing here
    is scenario-specific; a caller (e.g. `household_day.py`) supplies actual
    readings and physical constraints each tick and applies the returned
    setpoints to its own adapters."""

    def __init__(
        self,
        *,
        step_seconds: float,
        horizon_hours: float = DEFAULT_HORIZON_HOURS,
        forecast_period: timedelta = DEFAULT_FORECAST_PERIOD,
        forecast_max_lookback_cycles: int = DEFAULT_FORECAST_MAX_LOOKBACK_CYCLES,
    ) -> None:
        if step_seconds <= 0:
            raise ValueError("step_seconds must be positive")
        if horizon_hours <= 0:
            raise ValueError("horizon_hours must be positive")
        self._step_seconds = step_seconds
        self._dt_hours = step_seconds / 3600.0
        self._horizon_steps = max(1, round(horizon_hours * 3600.0 / step_seconds))

        history_length = int(
            forecast_period.total_seconds() / step_seconds * (forecast_max_lookback_cycles + 1)
        )
        self._pv_forecaster = SeasonalAverageForecaster(
            period=forecast_period, max_lookback_cycles=forecast_max_lookback_cycles
        )
        self._load_forecaster = SeasonalAverageForecaster(
            period=forecast_period, max_lookback_cycles=forecast_max_lookback_cycles
        )
        self._pv_history: deque[HistoricalPoint] = deque(maxlen=history_length)
        self._load_history: deque[HistoricalPoint] = deque(maxlen=history_length)

    @property
    def horizon_steps(self) -> int:
        return self._horizon_steps

    def _forecast_horizon(
        self,
        forecaster: SeasonalAverageForecaster,
        history: deque[HistoricalPoint],
        now: datetime,
        fallback: float,
    ) -> list[float]:
        return [
            forecaster.predict(
                history, now + timedelta(seconds=self._step_seconds * (k + 1)), fallback=fallback
            )
            for k in range(self._horizon_steps)
        ]

    def dispatch(
        self,
        *,
        now: datetime,
        pv_actual_w: float,
        load_actual_w: float,
        battery: BatteryState,
        tariff_at: Callable[[datetime], tuple[float, float]],
        ev: EVChargingState | None = None,
        max_import_w: list[float] | None = None,
    ) -> DispatchPlan:
        """Forecast the horizon from history recorded strictly before `now`,
        solve the LP, record `now`'s actuals for future calls, and return the
        plan. `tariff_at(t)` must return `(import_price_per_kwh,
        export_price_per_kwh)` for an arbitrary future time — e.g.
        `SimulatedGridConnectionAdapter.peek_price`.

        `max_import_w`, if given, is a per-horizon-step cap (same indexing as
        the forecasts — index 0 is the step after `now`) on how much power
        may be imported from the grid — the dashboard's "demand response"
        disturbance control, letting the LP proactively shift to
        battery/EV usage around the constrained window instead of a post-hoc
        clamp that would leave an unphysical energy shortfall. `None` (the
        default) leaves grid import unconstrained, as before this parameter
        existed."""
        pv_forecast = self._forecast_horizon(
            self._pv_forecaster, self._pv_history, now, pv_actual_w
        )
        load_forecast = self._forecast_horizon(
            self._load_forecaster, self._load_history, now, load_actual_w
        )

        prices = [
            tariff_at(now + timedelta(seconds=self._step_seconds * (k + 1)))
            for k in range(self._horizon_steps)
        ]
        import_prices = [price[0] for price in prices]
        export_prices = [price[1] for price in prices]

        battery_power_w, ev_power_w, cost = _solve(
            horizon_steps=self._horizon_steps,
            dt_hours=self._dt_hours,
            pv_forecast_w=pv_forecast,
            load_forecast_w=load_forecast,
            import_prices=import_prices,
            export_prices=export_prices,
            battery=battery,
            ev=ev,
            max_import_w=max_import_w,
        )

        # Only recorded *after* forecasting/solving — a forecaster must never
        # see the value it's predicting (mirrors live_engine.py's own rule).
        self._pv_history.append(HistoricalPoint(timestamp=now, value=pv_actual_w))
        self._load_history.append(HistoricalPoint(timestamp=now, value=load_actual_w))

        return DispatchPlan(
            battery_power_w=battery_power_w,
            ev_power_w=ev_power_w,
            pv_forecast_w=pv_forecast,
            load_forecast_w=load_forecast,
            projected_cost=cost,
        )


def _clamped_ev_targets(
    ev: EVChargingState, dt_hours: float
) -> dict[int, float]:
    """Cap each target SoC at what's actually reachable by that step given
    rated power and the plugged-in time available up to it, so the LP is
    never handed an EV constraint it can't satisfy (e.g. a deadline too
    close, or too little of the horizon plugged in, to deliver the requested
    energy) — this is deliberately proactive rather than catching the
    resulting `DispatchInfeasibleError`, so a merely-ambitious target still
    gets the best charge achievable instead of the whole tick's plan
    failing."""
    clamped = {}
    energy_wh = ev.state_of_charge * ev.capacity_wh
    for k in range(max(ev.target_soc_by_step, default=-1) + 1):
        if ev.plugged_in[k]:
            energy_wh = min(ev.capacity_wh, energy_wh + ev.rated_power_w * dt_hours)
        if k in ev.target_soc_by_step:
            achievable_soc = energy_wh / ev.capacity_wh
            clamped[k] = min(ev.target_soc_by_step[k], achievable_soc)
    return clamped


def _clamped_import_caps(
    max_import_w: list[float],
    pv_forecast_w: list[float],
    load_forecast_w: list[float],
    battery: BatteryState,
) -> list[float]:
    """Raise an unreachably-low demand-response import cap just enough to
    keep the tick feasible, same rationale as `_clamped_ev_targets`: a step
    whose forecasted load exceeds PV plus the battery's full rated power
    simply cannot be served under the requested cap without more supply than
    physically exists, so this floors each step's cap at that worst-case
    minimum rather than letting the whole tick's LP go infeasible over one
    unreachable step."""
    clamped = []
    for k, cap in enumerate(max_import_w):
        min_feasible_import_w = max(
            0.0, load_forecast_w[k] - pv_forecast_w[k] - battery.rated_power_w
        )
        clamped.append(max(cap, min_feasible_import_w))
    return clamped


def _solve(
    *,
    horizon_steps: int,
    dt_hours: float,
    pv_forecast_w: list[float],
    load_forecast_w: list[float],
    import_prices: list[float],
    export_prices: list[float],
    battery: BatteryState,
    ev: EVChargingState | None,
    max_import_w: list[float] | None = None,
) -> tuple[list[float], list[float] | None, float]:
    problem = pulp.LpProblem("economic_dispatch", pulp.LpMinimize)

    batt_charge = [
        pulp.LpVariable(f"batt_charge_{k}", lowBound=0, upBound=battery.rated_power_w)
        for k in range(horizon_steps)
    ]
    batt_discharge = [
        pulp.LpVariable(f"batt_discharge_{k}", lowBound=0, upBound=battery.rated_power_w)
        for k in range(horizon_steps)
    ]
    # A per-step demand-response import cap is just a tighter upBound on the
    # same variable the LP already solves for — the solver naturally shifts
    # to battery/EV usage around a constrained step rather than needing a
    # separate mechanism. Clamped first so an unreachably-low requested cap
    # degrades to "as low as physically achievable" instead of making the
    # whole tick's LP infeasible.
    import_caps = (
        _clamped_import_caps(max_import_w, pv_forecast_w, load_forecast_w, battery)
        if max_import_w is not None
        else None
    )
    grid_import = [
        pulp.LpVariable(
            f"grid_import_{k}",
            lowBound=0,
            upBound=import_caps[k] if import_caps is not None else None,
        )
        for k in range(horizon_steps)
    ]
    grid_export = [pulp.LpVariable(f"grid_export_{k}", lowBound=0) for k in range(horizon_steps)]

    ev_charge = None
    ev_targets: dict[int, float] = {}
    if ev is not None:
        ev_targets = _clamped_ev_targets(ev, dt_hours)
        ev_charge = [
            pulp.LpVariable(
                f"ev_charge_{k}", lowBound=0, upBound=ev.rated_power_w if ev.plugged_in[k] else 0.0
            )
            for k in range(horizon_steps)
        ]

    # Battery energy (Wh) recursion — same convention as
    # SimulatedBatteryAdapter.step(): charging efficiency applied going in,
    # discharge lossless at the terminals.
    battery_energy_wh = battery.state_of_charge * battery.capacity_wh
    for k in range(horizon_steps):
        battery_energy_wh = (
            battery_energy_wh
            + batt_charge[k] * dt_hours * battery.round_trip_efficiency
            - batt_discharge[k] * dt_hours
        )
        problem += battery_energy_wh >= 0.0, f"battery_soc_min_{k}"
        problem += battery_energy_wh <= battery.capacity_wh, f"battery_soc_max_{k}"

    if ev is not None and ev_charge is not None:
        ev_energy_wh = ev.state_of_charge * ev.capacity_wh
        for k in range(horizon_steps):
            ev_energy_wh = ev_energy_wh + ev_charge[k] * dt_hours
            problem += ev_energy_wh <= ev.capacity_wh, f"ev_soc_max_{k}"
            if k in ev_targets:
                problem += ev_energy_wh >= ev_targets[k] * ev.capacity_wh, f"ev_target_soc_{k}"

    for k in range(horizon_steps):
        controllable_load = batt_charge[k] + (ev_charge[k] if ev_charge is not None else 0.0)
        problem += (
            pv_forecast_w[k] + batt_discharge[k] + grid_import[k]
            == load_forecast_w[k] + controllable_load + grid_export[k]
        ), f"power_balance_{k}"

    # $/kWh * (W * h / 1000) = $.
    running_cost = pulp.lpSum(
        import_prices[k] * grid_import[k] * dt_hours / 1000.0
        - export_prices[k] * grid_export[k] * dt_hours / 1000.0
        for k in range(horizon_steps)
    )

    # Without crediting *some* value to energy still in the battery at the
    # end of the horizon, a finite-horizon LP has no reason not to drain it
    # for a quick sale on the very last step — nothing after the horizon
    # exists in its world, so "keep charge for later" has no representation
    # unless we add one. Valuing the final charge at the horizon's average
    # import price (a standard rolling-horizon/MPC device, sometimes called
    # a terminal value) makes exporting/discharging only worthwhile when the
    # price actually justifies it, not just because the horizon is ending.
    # Deliberately battery-only: the EV's target-SoC constraints already
    # capture "how much charge it needs to keep," so crediting its terminal
    # energy too would just bias it toward overcharging past target.
    terminal_price_per_kwh = sum(import_prices) / len(import_prices) if import_prices else 0.0
    terminal_value = (terminal_price_per_kwh / 1000.0) * battery_energy_wh

    problem += running_cost - terminal_value

    problem.solve(pulp.PULP_CBC_CMD(msg=0))
    status = pulp.LpStatus[problem.status]
    if status != "Optimal":
        raise DispatchInfeasibleError(status)

    battery_power_w = [
        (batt_discharge[k].value() or 0.0) - (batt_charge[k].value() or 0.0)
        for k in range(horizon_steps)
    ]
    ev_power_w = (
        [ev_charge[k].value() or 0.0 for k in range(horizon_steps)]
        if ev_charge is not None
        else None
    )
    cost = pulp.value(problem.objective) or 0.0

    return battery_power_w, ev_power_w, cost
