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

POWER_CONTROLLABLE_ADAPTERS = [
    FakeInverterAdapter,
    FakeBatteryStorageAdapter,
    FakeGeneratorAdapter,
    FakeLoadAdapter,
]


@pytest.mark.parametrize("adapter_cls", ALL_FAKE_ADAPTERS)
def test_base_contract(adapter_cls: type) -> None:
    assert_base_contract(adapter_cls("asset-1"))


@pytest.mark.parametrize("adapter_cls", POWER_CONTROLLABLE_ADAPTERS)
def test_power_controllable_contract(adapter_cls: type) -> None:
    adapter = adapter_cls("asset-1")
    assert isinstance(adapter, PowerControllable)
    assert_power_controllable_contract(adapter)


def test_state_of_charge_contract() -> None:
    adapter = FakeBatteryStorageAdapter("battery-1")
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
