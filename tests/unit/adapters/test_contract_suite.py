"""The adapter contract test suite (M1 visible result).

Every adapter class listed below is checked against the base AssetAdapter
contract, plus whichever capability contracts it declares support for. M2's
simulated adapters and M8's real adapters are expected to extend the
parametrizations here rather than write their own separate checks — that is
what proves "real vs. simulated, identical logic" for real rather than just
asserting it.
"""

from __future__ import annotations

import pytest

from microgridmanager.adapters.interface import (
    MeterReadable,
    PowerControllable,
    StateOfChargeReadable,
    Switchable,
)
from microgridmanager.adapters.simulated import (
    SimulatedBatteryAdapter,
    SimulatedGeneratorAdapter,
    SimulatedLoadAdapter,
    SimulatedPVAdapter,
)

from .contracts import (
    assert_base_contract,
    assert_meter_readable_contract,
    assert_power_controllable_contract,
    assert_state_of_charge_contract,
    assert_switchable_contract,
)
from .fakes import (
    FakeBatteryStorageAdapter,
    FakeBreakerAdapter,
    FakeGeneratorAdapter,
    FakeInverterAdapter,
    FakeLoadAdapter,
    FakeMeterAdapter,
)

ALL_FAKE_ADAPTERS = [
    FakeInverterAdapter,
    FakeBatteryStorageAdapter,
    FakeGeneratorAdapter,
    FakeLoadAdapter,
    FakeBreakerAdapter,
    FakeMeterAdapter,
]

# M2's simulated adapters extend this suite's parametrization rather than
# getting their own separate contract tests, per this module's docstring.
SIMULATED_ADAPTERS = [
    SimulatedPVAdapter,
    SimulatedBatteryAdapter,
    SimulatedLoadAdapter,
    SimulatedGeneratorAdapter,
]

ALL_ADAPTERS = ALL_FAKE_ADAPTERS + SIMULATED_ADAPTERS

# SimulatedGeneratorAdapter clamps setpoints to [0, rated_power_w] immediately
# (a genset can't run in reverse or over nameplate rating), so it doesn't
# satisfy the generic round-trip contract below — it gets a dedicated clamp
# test in tests/unit/adapters/simulated/test_generator.py instead.
POWER_CONTROLLABLE_ADAPTERS = [
    FakeInverterAdapter,
    FakeBatteryStorageAdapter,
    FakeGeneratorAdapter,
    FakeLoadAdapter,
    SimulatedBatteryAdapter,
    SimulatedLoadAdapter,
]

STATE_OF_CHARGE_ADAPTERS = [
    FakeBatteryStorageAdapter,
    SimulatedBatteryAdapter,
]


@pytest.mark.parametrize("adapter_cls", ALL_ADAPTERS)
def test_base_contract(adapter_cls: type) -> None:
    assert_base_contract(adapter_cls("asset-1"))


@pytest.mark.parametrize("adapter_cls", POWER_CONTROLLABLE_ADAPTERS)
def test_power_controllable_contract(adapter_cls: type) -> None:
    adapter = adapter_cls("asset-1")
    assert isinstance(adapter, PowerControllable)
    assert_power_controllable_contract(adapter)


@pytest.mark.parametrize("adapter_cls", STATE_OF_CHARGE_ADAPTERS)
def test_state_of_charge_contract(adapter_cls: type) -> None:
    adapter = adapter_cls("battery-1")
    assert isinstance(adapter, StateOfChargeReadable)
    assert_state_of_charge_contract(adapter)


def test_switchable_contract() -> None:
    adapter = FakeBreakerAdapter("breaker-1")
    assert isinstance(adapter, Switchable)
    assert_switchable_contract(adapter)


def test_meter_readable_contract() -> None:
    adapter = FakeMeterAdapter("meter-1")
    assert isinstance(adapter, MeterReadable)
    assert_meter_readable_contract(adapter)
