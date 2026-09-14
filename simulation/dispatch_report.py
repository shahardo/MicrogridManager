"""CLI: M7's visible result — run the household_day scenario twice (once with
the real dispatch engine, once with the original fixed self-consumption
rule) and report the total grid cost difference.

Lives under `simulation/` (not `scripts/`) like `runner.py`/`forecast_report.py`,
since it drives a named scenario — run it with `python -m
simulation.dispatch_report` (or `make dispatch-report`).

Example:
    python -m simulation.dispatch_report --duration-hours 24
"""

from __future__ import annotations

import argparse

from simulation.scenarios import household_day


def total_grid_cost_usd(rows: list[dict], step_seconds: float) -> float:
    """Sum each tick's grid cost: import costs at the import price, export
    earns (i.e. subtracts) at the export price — the same accounting
    `household_day`'s dispatch engine minimizes internally."""
    dt_hours = step_seconds / 3600.0
    cost = 0.0
    for row in rows:
        power_w = row["grid_power_w"]
        if power_w >= 0.0:
            cost += row["grid_import_price_per_kwh"] * power_w * dt_hours / 1000.0
        else:
            cost -= row["grid_export_price_per_kwh"] * (-power_w) * dt_hours / 1000.0
    return cost


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Compare M7 dispatch-optimized cost against the naive self-consumption rule."
    )
    parser.add_argument("--step-seconds", type=float, default=300.0)
    parser.add_argument("--duration-hours", type=float, default=24.0)
    args = parser.parse_args(argv)

    dispatch_rows = household_day.run(
        step_seconds=args.step_seconds, duration_hours=args.duration_hours, dispatch_enabled=True
    )
    naive_rows = household_day.run(
        step_seconds=args.step_seconds, duration_hours=args.duration_hours, dispatch_enabled=False
    )

    dispatch_cost = total_grid_cost_usd(dispatch_rows, args.step_seconds)
    naive_cost = total_grid_cost_usd(naive_rows, args.step_seconds)
    savings = naive_cost - dispatch_cost
    savings_pct = (savings / naive_cost * 100.0) if naive_cost != 0.0 else float("nan")

    print(f"household_day, {args.duration_hours:.0f}h at {args.step_seconds:.0f}s steps\n")
    print(f"{'strategy':<30} {'grid cost':>12}")
    print(f"{'naive self-consumption':<30} {'$' + f'{naive_cost:,.2f}':>12}")
    print(f"{'dispatch-optimized (M7)':<30} {'$' + f'{dispatch_cost:,.2f}':>12}")
    print(f"\nSavings: ${savings:,.2f} ({savings_pct:.1f}%)")


if __name__ == "__main__":
    main()
