# MicrogridManager

A **Distributed Microgrid Management System (DMMS)**: software for managing
microgrids at any scale — from a single small local microgrid, to a regional
cluster, to utility-scale orchestration of many independently-owned
microgrids — using one recurring architectural pattern.

## What is a microgrid?

A locally controllable cluster of generation (solar, wind, generators),
storage (batteries), and load, able to run either grid-connected or
**islanded** (self-sufficient) during an outage.

## What this system does

At every scale, the system provides:

- **Resilience** — islanding, black-start, load prioritization to keep
  critical loads powered through an outage.
- **Optimization** — economic dispatch and storage scheduling to minimize
  cost and maximize use of local renewables.
- **Grid services** — aggregating flexible capacity across microgrids to
  offer demand response, capacity, or regulation to a utility or market.

It supports both real hardware control and a simulation/digital-twin mode
using the same control logic, and is designed for multi-tenant ownership
(independently-owned microgrids) over unreliable, intermittent network links
between sites and any regional/utility coordination layer.

See [`docs/architecture.md`](docs/architecture.md) for the full conceptual
design, including the three-tier architecture, core subsystems, and phased
implementation roadmap.

## Status

**Design phase.** The architecture is defined; implementation has not started
yet. The roadmap begins with a single-site controller running against
simulated hardware (see `docs/architecture.md` §6).

## Development conventions

This project keeps `README.md` and `CLAUDE.md` up to date with every change,
requires a visible result (a test or a UI/output update) for every change,
and tests every change before it's considered done. See `CLAUDE.md` for
details.
