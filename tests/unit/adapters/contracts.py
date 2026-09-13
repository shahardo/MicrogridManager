"""Shared contract assertions for AssetAdapter implementations.

Every adapter — fake, simulated (M2), or real (M8) — must satisfy these.
Keeping them as plain functions (rather than a `Test*` class) lets any test
file, for any adapter implementation, import and reuse them directly.
"""

from __future__ import annotations

from microgridmanager.adapters.interface import (
    AssetAdapter,
    AssetState,
    MeterReadable,
    MeterState,
    PowerControllable,
    StateOfChargeReadable,
    Switchable,
)


def assert_base_contract(adapter: AssetAdapter) -> None:
    assert isinstance(adapter.asset_id, str) and adapter.asset_id
    state = adapter.get_state()
    assert isinstance(state, AssetState)
    assert state.asset_id == adapter.asset_id
    assert state.asset_type == adapter.asset_type
    assert state.online is True


def assert_power_controllable_contract(adapter: PowerControllable) -> None:
    adapter.set_active_power_w(1234.0)
    assert adapter.get_state().active_power_w == 1234.0  # type: ignore[attr-defined]

    adapter.set_active_power_w(-500.0)
    assert adapter.get_state().active_power_w == -500.0  # type: ignore[attr-defined]


def assert_state_of_charge_contract(adapter: StateOfChargeReadable) -> None:
    soc = adapter.get_state_of_charge()
    assert 0.0 <= soc <= 1.0


def assert_switchable_contract(adapter: Switchable) -> None:
    adapter.close()
    assert adapter.is_closed() is True

    adapter.open()
    assert adapter.is_closed() is False


def assert_meter_readable_contract(adapter: MeterReadable) -> None:
    reading = adapter.get_reading()
    assert isinstance(reading, MeterState)
