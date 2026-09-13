"""Simulated grid connection adapter (M4): the point-of-common-coupling (PCC)
pseudo-asset.

Unlike the other M4 devices, the grid connection isn't independently
controllable — it simply meters whatever net power flows across the PCC
(import when the site's other assets fall short, export when they produce a
surplus) and reports the current time-of-use tariff price. A scenario (or,
later, the site controller) calls `set_net_power_w()` once it has balanced
every other asset for the tick, then `step()` to integrate cumulative
import/export energy — the same "record setpoint, then integrate" split used
by the M2 battery/generator adapters.
"""

from __future__ import annotations

from datetime import datetime

from microgridmanager.adapters.interface import AssetAdapter, AssetType, GridState

from .clock import SimulationClock
from .profiles import TariffProfile, time_of_use_tariff_profile


class SimulatedGridConnectionAdapter(AssetAdapter):
    def __init__(
        self,
        asset_id: str,
        *,
        clock: SimulationClock | None = None,
        tariff_profile: TariffProfile | None = None,
    ) -> None:
        self._asset_id = asset_id
        self._clock = clock or SimulationClock()
        self._tariff_profile = tariff_profile or time_of_use_tariff_profile()
        self._active_power_w = 0.0
        self._cumulative_import_wh = 0.0
        self._cumulative_export_wh = 0.0

    @property
    def asset_id(self) -> str:
        return self._asset_id

    @property
    def asset_type(self) -> AssetType:
        return AssetType.GRID

    def set_net_power_w(self, watts: float) -> None:
        """Record this tick's net PCC power: positive imports, negative
        exports."""
        self._active_power_w = watts

    def peek_price(self, at: datetime) -> tuple[float, float]:
        """Read the (import, export) tariff price at an arbitrary time —
        e.g. one step ahead — without affecting metered state. Used by the
        M4 dashboard's placeholder "projected price" decision variable."""
        return self._tariff_profile(at)

    def get_state(self) -> GridState:
        import_price, export_price = self._tariff_profile(self._clock.now)
        return GridState(
            asset_id=self._asset_id,
            asset_type=self.asset_type,
            timestamp=self._clock.now,
            online=True,
            active_power_w=self._active_power_w,
            cumulative_import_wh=self._cumulative_import_wh,
            cumulative_export_wh=self._cumulative_export_wh,
            import_price_per_kwh=import_price,
            export_price_per_kwh=export_price,
        )

    def step(self, dt_seconds: float) -> None:
        dt_hours = dt_seconds / 3600.0
        if self._active_power_w >= 0.0:
            self._cumulative_import_wh += self._active_power_w * dt_hours
        else:
            self._cumulative_export_wh += -self._active_power_w * dt_hours
