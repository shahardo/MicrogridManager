"""M4 dashboard placeholders: inline stand-ins for the forecasting (M6) and
dispatch (M7) modules, which don't exist yet.

These exist purely so the forecast and decision-variables panels have real
data to chart before the real modules land — M6/M7 replace the callers of
these functions with their real models without changing the panels
themselves (the series names stay the same), per the Phase 1 plan's tracked
placeholder-to-real swaps.
"""

from __future__ import annotations


def persistence_forecast(previous_row: dict | None, key: str, fallback: float) -> float:
    """The naive "persistence" forecast: predict this step's value as
    whatever was actually observed last step (or `fallback` on the very
    first step, when there's no history yet)."""
    if previous_row is None:
        return fallback
    return previous_row[key]
