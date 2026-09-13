# Conceptual Design: Distributed Microgrid Management System (DMMS)

## Context

This document is a conceptual/architecture-level design for a **Distributed Microgrid Management System (DMMS)**. The system must span the full range of scale — from a single small local microgrid, up through a small regional cluster of microgrids, up to utility-scale orchestration of many interconnected microgrids — using one recurring ("fractal") architectural pattern rather than three separate systems. Four additional requirements shape the design:

1. **All three objectives are first-class from day one**: resilience/continuity of supply, cost & renewables optimization, and grid-services/virtual-power-plant participation.
2. **Both real hardware control and simulation/digital-twin** must be supported by the same control logic.
3. **Multi-tenant**: independently-owned microgrids, with strict authority/data isolation between tenants and tiers.
4. **Unreliable/intermittent connectivity** between tiers must be assumed — no tier's safety-critical function may depend on a live link to the tier above it.

## 1. What a microgrid is, and what a management system needs to do

**Microgrid**: a locally controllable cluster of generation (PV, wind, diesel/gas gensets), storage (batteries), and load, bounded by one or more points of common coupling (PCC) to the upstream grid, able to run **grid-connected** or **islanded**. The defining property: it has **local control authority** — it can balance and protect itself without needing anyone's permission.

**Distributed Microgrid Management System (DMMS)**: manages a *population* of microgrids at growing scales. At every tier it must provide the same three things: local safety/continuity for what it directly controls, optimization within its scope, and the ability to coordinate with (but never depend on) other tiers.

**Three tiers, one recurring pattern:**

| Tier | Scope | Loop cadence | Talks to |
|---|---|---|---|
| Site / Local | one microgrid, one tenant | ms–s (protection), s–min (dispatch) | physical assets; regional tier (optional) |
| Regional | a cluster of microgrids | s–min | sites; utility tier (optional) |
| Utility / Interconnection | many regions, market/ISO-facing | min–hr (regulation signals pass through faster) | regions; external DERMS/ISO/market systems |

Each tier is the *same kind of node* — it owns a control/optimization loop, a local state store, an authority boundary, and up/down communication interfaces. That's what lets the design scale from one site to a region to a utility-wide fleet, and lets a region itself later be aggregated by another region, without re-architecting.

## 2. Needs, by tier

**Site controller** needs: real-time asset monitoring; islanding detection & anti-islanding compliance; black-start sequencing; load prioritization/shedding; local frequency/voltage regulation while islanded; local economic dispatch; short-horizon forecasting; full autonomy with zero connectivity; local operator override that's always absolute; store-and-forward telemetry; and the ability to accept (never blindly obey) higher-tier requests.

**Regional aggregator** needs: fleet visibility (tenant-isolated); allocation of a regional objective into per-site *requests* (not commands); aggregated medium-horizon forecasting; grid-services bundling across sites; contract/consent tracking of what each site allows; regional autonomy if the utility link drops; rollup telemetry.

**Utility/interconnection layer** needs: DERMS/ISO/market integration (OpenADR, IEEE 2030.5); translation of market/grid signals into regional objectives; VPP-style capacity aggregation and settlement-grade reporting; day-ahead/multi-day forecasting; multi-region policy; and the same graceful-degradation guarantee — if this layer disappears, regions and sites keep running exactly as before.

## 3. Core subsystems that fulfill these needs

- **Asset/device abstraction layer**: a canonical asset model (inverter, BESS, generator, breaker, meter, EV charger) behind a protocol-agnostic interface (`setActivePower`, `getStateOfCharge`, `openBreaker`, …). Protocol adapters (Modbus, DNP3, IEC 61850, SunSpec, OCPP, MQTT) plug into it; adding a protocol never touches control/optimization code.
- **Real vs. simulated adapters behind the identical interface** — this is the single mechanism that satisfies "real control + simulation from the same logic". A digital twin is just a site controller wired to simulated adapters.
- **Local protection/control logic**: an explicit state machine (Normal ↔ Islanding-transition ↔ Islanded ↔ Black-start ↔ Restoration) — safety-critical, deterministic, always on-site, never dependent on any link above it.
- **Forecasting**: common interface, per-tier horizon (short at site, aggregated at regional, day-ahead at utility), swappable models (start statistical, add ML later).
- **Optimization/dispatch engine**: one reusable rolling-horizon engine (economic dispatch / MPC-style), parameterized differently per tier (horizon, objective weights, hard-vs-advisory constraints). Its output always passes through the local protection layer as a final safety gate — dispatch proposes, protection disposes.
- **State sync / eventual consistency**: each tier owns its local authoritative state; commands flow down as time-bounded, expiring "requested setpoints," never blocking RPCs; telemetry flows up via store-and-forward, event-sourced, idempotent-replay logs. This is what makes intermittent connectivity a design default rather than a special case — on comms loss, every tier reverts automatically to a pre-configured local default.
- **Multi-tenant identity & authority model**: capability-grant based, not role hierarchy (e.g. "region X may request ≤20kW curtailment from site Y's non-critical load, 2–6pm"), scoped/revocable/auditable. Local override is structurally absolute — no code path lets an external command bypass the protection layer. Tenant data isolation by default; aggregated/anonymized views upward unless a tenant opts in to sharing.
- **Grid-services/market interface**: a translation layer (mainly at the utility tier) speaking OpenADR, IEEE 2030.5, IEC 61850, IEEE 1547 compliance, DERMS integration — converts external signals into the same internal request/consent objects used between site and region, so it's not a special case either.
- **Security**: OT/IT network segmentation, zero-trust mTLS between every tier/tenant boundary, outbound-only connections from sites (no inbound exposure, and it naturally fits the intermittent-link story), signed edge firmware/control updates, full audit logging of every cross-boundary command.
- **Observability**: per-tier dashboards scoped to that tier's authority (site = full detail, regional = tenant-consented aggregates, utility = portfolio/program view); an explicit "what was requested vs. what actually happened and why" audit view, since multiple tiers can influence one site.

## 4. Reference architecture

Every tier is composed of the same internal blocks (site tier additionally has the adapter + protection layers, since only it touches hardware):

```
[Adapters: real|simulated] → [Protection/Safety state machine] → [Local state store]
        ↕                                                              ↕
                    [Forecasting] ↔ [Optimization/Dispatch]
                                        ↕
                         [Sync layer: store-and-forward, event log]
                                        ↕
        down: manages tier below  ⟷  up: reports to / requests from tier above
```

**Communication pattern**: outbound-only broker connections (sites → regional broker → utility broker) — avoids inbound exposure and fits reconnect/resume naturally. Event-driven, not RPC: telemetry streams up; requests flow down as versioned, expiring "desired state" messages. If a request's validity window lapses with no renewal, the receiving tier reverts to its local default automatically — fail-safe by construction.

**Control pattern**: hierarchical, receding-horizon coordination — each tier optimizes over its own horizon and passes down *targets*, never commands; the tier below always keeps a locally-feasible fallback (do-nothing/last-known-good/safe-default) it can apply instantly.

**Compute placement**: site tier = edge, co-located with hardware (protection cannot depend on WAN); regional tier = edge or cloud (flexible, since it's seconds-to-minutes and already working off buffered data); utility tier = cloud (long-horizon, integration-heavy).

**Key trade-off calls**:
- Hierarchical/federated control over full centralization — centralized control breaks the moment the WAN link does, which directly violates the resilience requirement.
- Advisory, revocable requests over either pure top-down commands or fully uncoordinated sites — needed to get both local resilience *and* grid-services aggregation.
- Event-sourced/store-and-forward sync over synchronous state replication — synchronous replication can't survive an unreliable WAN without blocking or silently diverging.

## 5. Non-functional considerations

Fail-safe defaults everywhere (no "wait for instructions" states); latency budgets tightening with tier (ms–s protection at site → min–hr at utility, with sub-minute regulation signals passed straight through); a deliberately small/cheap site-controller footprint so it works at "one small local microgrid" scale; horizontally-scalable regional/utility tiers; adapters and grid-service programs as plugins for extensibility; alignment with IEEE 1547, IEC 61850, DNP3, SunSpec, Modbus, OCPP, OpenADR, IEEE 2030.5, and IEC 62443 (OT security); and simulation-first testability as a non-negotiable property, since production and simulated code share the same control/optimization logic.

## 6. Phased roadmap (build order, no rewrite between phases)

1. **Single-site controller + simulation** — canonical asset model, simulated adapters first, then real adapters (Modbus/SunSpec); protection state machine validated in simulation; local forecasting + dispatch; local dashboard. Shippable on its own for real deployments or pure planning/sizing use.
2. **Multi-tenant hardening + sync layer** — identity/capability-grant model, tenant isolation, outbound-only broker comms, event-sourced sync, security hardening. Built *before* regional aggregation exists so it's never retrofitted.
3. **Regional aggregator** — same node pattern reused with a regional horizon/objective; request/response to sites over the Phase 2 comms layer; consent/contract management; first grid-services bundling; validated against multiple simulated sites before any real multi-site rollout.
4. **Utility/interconnection tier** — market/DERMS/OpenADR/IEEE 2030.5 translation on the same request/objective abstraction; settlement-grade reporting; portfolio dashboards.
5. **Recursive scaling & hardening** — confirm regions can be aggregated by another region (the fractal property in practice); richer forecasting/optimization; broader protocol coverage; digital twin maturing into a standalone planning/sizing tool.

The two hardest architectural bets — the real/simulated adapter split and the eventual-consistency multi-tenant comms layer — are front-loaded in Phases 1–2, so everything from Phase 3 onward is mostly reuse and reparameterization, not new architecture.
