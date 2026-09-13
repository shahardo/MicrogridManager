"""Minimal in-memory fake adapters used to validate the AssetAdapter contract.

These are test doubles, not the real simulated adapters (those arrive in M2)
— they exist purely to prove the interface defined in M1 is implementable and
that the contract test suite in this package actually exercises it.
"""

from __future__ import annotations

from datetime import datetime, timezone

from microgridmanager.adapters.interface import (
    AssetAdapter,
    AssetType,
    BatteryState,
    BreakerState,
    GeneratorState,
    InverterState,
    LoadState,
    MeterReadable,
    MeterState,
    PowerControllable,
    StateOfChargeReadable,
    Switchable,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class FakeInverterAdapter(AssetAdapter, PowerControllable):
    def __init__(self, asset_id: str) -> None:
        self._asset_id = asset_id
        self._active_power_w = 0.0

    @property
    def asset_id(self) -> str:
        return self._asset_id

    @property
    def asset_type(self) -> AssetType:
        return AssetType.INVERTER

    def get_state(self) -> InverterState:
        return InverterState(
            asset_id=self._asset_id,
            asset_type=self.asset_type,
            timestamp=_now(),
            online=True,
            active_power_w=self._active_power_w,
        )

    def set_active_power_w(self, watts: float) -> None:
        self._active_power_w = watts


class FakeBatteryStorageAdapter(AssetAdapter, PowerControllable, StateOfChargeReadable):
    def __init__(self, asset_id: str, capacity_wh: float = 10_000.0) -> None:
        self._asset_id = asset_id
        self._active_power_w = 0.0
        self._capacity_wh = capacity_wh
        self._energy_wh = capacity_wh * 0.5

    @property
    def asset_id(self) -> str:
        return self._asset_id

    @property
    def asset_type(self) -> AssetType:
        return AssetType.BATTERY_STORAGE

    def get_state(self) -> BatteryState:
        return BatteryState(
            asset_id=self._asset_id,
            asset_type=self.asset_type,
            timestamp=_now(),
            online=True,
            active_power_w=self._active_power_w,
            state_of_charge=self.get_state_of_charge(),
        )

    def set_active_power_w(self, watts: float) -> None:
        self._active_power_w = watts

    def get_state_of_charge(self) -> float:
        return self._energy_wh / self._capacity_wh


class FakeGeneratorAdapter(AssetAdapter, PowerControllable):
    def __init__(self, asset_id: str) -> None:
        self._asset_id = asset_id
        self._active_power_w = 0.0

    @property
    def asset_id(self) -> str:
        return self._asset_id

    @property
    def asset_type(self) -> AssetType:
        return AssetType.GENERATOR

    def get_state(self) -> GeneratorState:
        return GeneratorState(
            asset_id=self._asset_id,
            asset_type=self.asset_type,
            timestamp=_now(),
            online=True,
            active_power_w=self._active_power_w,
        )

    def set_active_power_w(self, watts: float) -> None:
        self._active_power_w = watts


class FakeLoadAdapter(AssetAdapter, PowerControllable):
    def __init__(self, asset_id: str) -> None:
        self._asset_id = asset_id
        self._active_power_w = 0.0

    @property
    def asset_id(self) -> str:
        return self._asset_id

    @property
    def asset_type(self) -> AssetType:
        return AssetType.LOAD

    def get_state(self) -> LoadState:
        return LoadState(
            asset_id=self._asset_id,
            asset_type=self.asset_type,
            timestamp=_now(),
            online=True,
            active_power_w=self._active_power_w,
        )

    def set_active_power_w(self, watts: float) -> None:
        self._active_power_w = watts


class FakeBreakerAdapter(AssetAdapter, Switchable):
    def __init__(self, asset_id: str) -> None:
        self._asset_id = asset_id
        self._closed = False

    @property
    def asset_id(self) -> str:
        return self._asset_id

    @property
    def asset_type(self) -> AssetType:
        return AssetType.BREAKER

    def get_state(self) -> BreakerState:
        return BreakerState(
            asset_id=self._asset_id,
            asset_type=self.asset_type,
            timestamp=_now(),
            online=True,
            is_closed=self._closed,
        )

    def open(self) -> None:
        self._closed = False

    def close(self) -> None:
        self._closed = True

    def is_closed(self) -> bool:
        return self._closed


class FakeMeterAdapter(AssetAdapter, MeterReadable):
    def __init__(self, asset_id: str) -> None:
        self._asset_id = asset_id

    @property
    def asset_id(self) -> str:
        return self._asset_id

    @property
    def asset_type(self) -> AssetType:
        return AssetType.METER

    def get_state(self) -> MeterState:
        return self.get_reading()

    def get_reading(self) -> MeterState:
        return MeterState(
            asset_id=self._asset_id,
            asset_type=self.asset_type,
            timestamp=_now(),
            online=True,
            active_power_w=0.0,
            cumulative_energy_wh=0.0,
        )
