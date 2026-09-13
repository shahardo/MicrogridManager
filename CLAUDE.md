# CLAUDE.md

Guidance for Claude Code (or any agent) working in this repository.

## Project

MicrogridManager is a **Distributed Microgrid Management System (DMMS)**. See
[`docs/architecture.md`](docs/architecture.md) for the full conceptual design: a
three-tier (site → regional → utility) hierarchical architecture that scales from
a single microgrid to utility-wide orchestration of many microgrids, unifies real
hardware control and simulation behind one control interface, and assumes
multi-tenant ownership with intermittent connectivity between tiers.

Current status: design phase. No production code exists yet; implementation
follows the phased roadmap in `docs/architecture.md` §6, starting with a
single-site controller + simulation.

## Working conventions (always follow these)

1. **Keep `CLAUDE.md` and `README.md` current.** After adding or changing any
   functionality, update both files in the same change: `README.md` for
   user-facing capability/usage, `CLAUDE.md` for anything a future agent
   session needs to know (new modules, conventions, how to run things).
2. **Visibility first.** Every functional change must produce a visible,
   checkable result — either a new/updated automated test that exercises the
   change, or a visible UI/output update (e.g. a dashboard view, CLI output,
   simulation report). No change should land with only internal code and
   nothing to observe or verify.
3. **Always test each update.** Before considering any change done, run the
   relevant tests (and add them if they don't exist yet) and confirm they
   pass. Don't rely on inspection alone.
4. Prefer extending the shared abstractions defined in `docs/architecture.md`
   (canonical asset/adapter interface, real-vs-simulated adapters, the single
   reusable optimization/dispatch engine, the capability-grant authority
   model) over introducing parallel, one-off mechanisms.
5. Safety-critical logic (protection/islanding/black-start state machine)
   must remain deterministic and independent of any network link — never make
   it depend on connectivity to a regional/utility tier.

## Repository layout

- `docs/architecture.md` — the conceptual design and phased roadmap.
- (Source directories will be documented here as they are added.)
