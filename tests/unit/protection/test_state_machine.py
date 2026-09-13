"""M5 exit-criteria tests: every transition's guard condition, and the
load-shedding priority order."""

from __future__ import annotations

from microgridmanager.protection import ProtectionController, ProtectionState, SheddableLoad

CRITICAL = SheddableLoad(name="critical", priority=0, demand_w=1000.0)
MEDIUM = SheddableLoad(name="medium", priority=1, demand_w=500.0)
LOW = SheddableLoad(name="low", priority=2, demand_w=800.0)
LOADS = [CRITICAL, MEDIUM, LOW]


def test_starts_normal() -> None:
    controller = ProtectionController()
    assert controller.state == ProtectionState.NORMAL


def test_normal_stays_normal_while_grid_connected() -> None:
    controller = ProtectionController()
    decision = controller.decide(grid_connected=True, available_power_w=10_000.0, loads=LOADS)
    assert decision.state == ProtectionState.NORMAL
    assert all(decision.served.values())
    assert decision.grid_exchange_allowed() is True


def test_grid_loss_enters_islanding_transition_then_islanded() -> None:
    controller = ProtectionController()

    decision = controller.decide(grid_connected=False, available_power_w=10_000.0, loads=LOADS)
    assert decision.state == ProtectionState.ISLANDING_TRANSITION
    # The grid backstops any shortfall during the transition tick itself.
    assert all(decision.served.values())
    assert decision.grid_exchange_allowed() is False

    decision = controller.decide(grid_connected=False, available_power_w=10_000.0, loads=LOADS)
    assert decision.state == ProtectionState.ISLANDED


def test_islanding_transition_recovers_to_normal_if_grid_returns_immediately() -> None:
    controller = ProtectionController()
    controller.decide(grid_connected=False, available_power_w=10_000.0, loads=LOADS)
    assert controller.state == ProtectionState.ISLANDING_TRANSITION

    decision = controller.decide(grid_connected=True, available_power_w=10_000.0, loads=LOADS)
    assert decision.state == ProtectionState.NORMAL


def _island(controller: ProtectionController, *, available_power_w: float = 10_000.0) -> None:
    controller.decide(grid_connected=False, available_power_w=available_power_w, loads=LOADS)
    controller.decide(grid_connected=False, available_power_w=available_power_w, loads=LOADS)
    assert controller.state == ProtectionState.ISLANDED


def test_islanded_sheds_low_priority_loads_first_when_short_on_power() -> None:
    controller = ProtectionController()
    _island(controller)

    # 1700W available: covers critical (1000W) and medium (500W) but not all
    # three (1000+500+800=2300W total demand), so low is shed.
    decision = controller.decide(grid_connected=False, available_power_w=1700.0, loads=LOADS)
    assert decision.state == ProtectionState.ISLANDED
    assert decision.served == {"critical": True, "medium": True, "low": False}


def test_islanded_sheds_everything_but_critical_when_tighter() -> None:
    controller = ProtectionController()
    _island(controller)

    decision = controller.decide(grid_connected=False, available_power_w=1000.0, loads=LOADS)
    assert decision.served == {"critical": True, "medium": False, "low": False}


def test_islanded_escalates_to_black_start_when_even_critical_cant_be_met() -> None:
    controller = ProtectionController()
    _island(controller)

    decision = controller.decide(grid_connected=False, available_power_w=500.0, loads=LOADS)
    assert decision.state == ProtectionState.BLACK_START
    assert all(served is False for served in decision.served.values())


def test_black_start_does_not_recover_at_exactly_critical_demand() -> None:
    """The recovery margin (hysteresis, default 10%) means barely clearing
    critical demand isn't enough to leave BLACK_START — without this, a
    slowly-rising supply hovering right at the threshold for several ticks
    (e.g. PV ramping up at dawn) would flap between ISLANDED and BLACK_START
    every tick it crossed back and forth, the same chattering problem
    SimulatedWaterHeaterAdapter's own hysteresis avoids."""
    controller = ProtectionController()
    _island(controller)
    controller.decide(grid_connected=False, available_power_w=500.0, loads=LOADS)
    assert controller.state == ProtectionState.BLACK_START

    decision = controller.decide(grid_connected=False, available_power_w=1000.0, loads=LOADS)
    assert decision.state == ProtectionState.BLACK_START


def test_black_start_recovers_to_islanded_once_comfortably_above_critical_demand() -> None:
    controller = ProtectionController()
    _island(controller)
    controller.decide(grid_connected=False, available_power_w=500.0, loads=LOADS)
    assert controller.state == ProtectionState.BLACK_START

    decision = controller.decide(grid_connected=False, available_power_w=1200.0, loads=LOADS)
    assert decision.state == ProtectionState.ISLANDED
    assert decision.served["critical"] is True


def test_black_start_stays_black_start_while_still_short() -> None:
    controller = ProtectionController()
    _island(controller)
    controller.decide(grid_connected=False, available_power_w=200.0, loads=LOADS)
    assert controller.state == ProtectionState.BLACK_START

    decision = controller.decide(grid_connected=False, available_power_w=200.0, loads=LOADS)
    assert decision.state == ProtectionState.BLACK_START
    assert all(served is False for served in decision.served.values())


def test_grid_return_enters_restoration_then_normal_from_islanded() -> None:
    controller = ProtectionController()
    _island(controller)

    decision = controller.decide(grid_connected=True, available_power_w=10_000.0, loads=LOADS)
    assert decision.state == ProtectionState.RESTORATION
    assert all(decision.served.values())
    assert decision.grid_exchange_allowed() is True

    decision = controller.decide(grid_connected=True, available_power_w=10_000.0, loads=LOADS)
    assert decision.state == ProtectionState.NORMAL


def test_grid_return_enters_restoration_from_black_start() -> None:
    controller = ProtectionController()
    _island(controller)
    controller.decide(grid_connected=False, available_power_w=0.0, loads=LOADS)
    assert controller.state == ProtectionState.BLACK_START

    decision = controller.decide(grid_connected=True, available_power_w=0.0, loads=LOADS)
    assert decision.state == ProtectionState.RESTORATION
    assert all(decision.served.values())


def test_no_loads_never_needs_black_start() -> None:
    controller = ProtectionController()
    controller.decide(grid_connected=False, available_power_w=0.0, loads=[])
    decision = controller.decide(grid_connected=False, available_power_w=0.0, loads=[])
    assert decision.state == ProtectionState.ISLANDED
    assert decision.served == {}
