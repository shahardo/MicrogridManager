"""Canonical asset model and the AssetAdapter interface.

This is the seam that decouples control/optimization logic from both the
protocol used to reach a device (Modbus, DNP3, ...) and from whether the
device is real or simulated. New asset kinds or protocols are added as new
adapters/capabilities here; nothing above this layer should need to change.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class AssetType(str, Enum):
    INVERTER = "inverter"
    BATTERY_STORAGE = "battery_storage"
    GENERATOR = "generator"
    LOAD = "load"
    BREAKER = "breaker"
    METER = "meter"
    EV_CHARGER = "ev_charger"
    WATER_HEATER = "water_heater"
    GRID = "grid"


@dataclass(frozen=True, kw_only=True)
class AssetState:
    """Fields every asset state carries, regardless of asset type."""

    asset_id: str
    asset_type: AssetType
    timestamp: datetime
    online: bool


@dataclass(frozen=True, kw_only=True)
class InverterState(AssetState):
    active_power_w: float


@dataclass(frozen=True, kw_only=True)
class BatteryState(AssetState):
    active_power_w: float
    state_of_charge: float  # 0.0-1.0


@dataclass(frozen=True, kw_only=True)
class GeneratorState(AssetState):
    active_power_w: float


@dataclass(frozen=True, kw_only=True)
class LoadState(AssetState):
    active_power_w: float


@dataclass(frozen=True, kw_only=True)
class BreakerState(AssetState):
    is_closed: bool


@dataclass(frozen=True, kw_only=True)
class MeterState(AssetState):
    active_power_w: float
    cumulative_energy_wh: float


@dataclass(frozen=True, kw_only=True)
class EVChargerState(AssetState):
    """A session-based controllable-load asset (M4): a car plugs in, charges
    toward a target state of charge by a deadline, then unplugs."""

    active_power_w: float
    plugged_in: bool
    charging: bool
    state_of_charge: float  # 0.0-1.0
    target_state_of_charge: float
    time_remaining_s: float | None  # None when not plugged in


@dataclass(frozen=True, kw_only=True)
class WaterHeaterState(AssetState):
    """A thermal-storage-style controllable-load asset (M4): a virtual tank
    depletes against a hot-water draw schedule and is reheated by cycling an
    electric heating element."""

    active_power_w: float  # electrical draw of the heating element
    tank_energy_fraction: float  # 0.0-1.0
    heating: bool


@dataclass(frozen=True, kw_only=True)
class GridState(AssetState):
    """The point-of-common-coupling pseudo-asset (M4): import/export
    metering plus the current time-of-use tariff price."""

    active_power_w: float  # positive = importing from the grid, negative = exporting
    cumulative_import_wh: float
    cumulative_export_wh: float
    import_price_per_kwh: float
    export_price_per_kwh: float


class AssetAdapter(ABC):
    """Base interface every adapter — real or simulated — must implement."""

    @property
    @abstractmethod
    def asset_id(self) -> str: ...

    @property
    @abstractmethod
    def asset_type(self) -> AssetType: ...

    @abstractmethod
    def get_state(self) -> AssetState: ...


class PowerControllable(ABC):
    """Capability: the asset accepts an active-power setpoint (inverter,
    battery, generator, controllable load)."""

    @abstractmethod
    def set_active_power_w(self, watts: float) -> None: ...


class StateOfChargeReadable(ABC):
    """Capability: the asset reports a state of charge in [0.0, 1.0]."""

    @abstractmethod
    def get_state_of_charge(self) -> float: ...


class Switchable(ABC):
    """Capability: the asset can be opened/closed (e.g. a breaker)."""

    @abstractmethod
    def open(self) -> None: ...

    @abstractmethod
    def close(self) -> None: ...

    @abstractmethod
    def is_closed(self) -> bool: ...


class MeterReadable(ABC):
    """Capability: the asset provides metering readings."""

    @abstractmethod
    def get_reading(self) -> MeterState: ...
