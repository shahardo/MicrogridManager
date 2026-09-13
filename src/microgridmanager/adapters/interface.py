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
