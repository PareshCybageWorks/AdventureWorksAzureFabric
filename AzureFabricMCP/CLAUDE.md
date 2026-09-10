# AzureFabricMCP — Claude configuration

What an AI assistant should load, and when, working in this repository.

## Corrected 2026-08-27

This file previously advertised "60+ professional skills" across five
workload domains, with a full auto-discovery scheme and learning paths built
around them. Those skills were never written — see `skills/README.md` for
what actually exists (two files). The claim is replaced rather than left
standing, because a config file that tells Claude to load a skill that isn't
there wastes a turn discovering that, every time.

## Where the real knowledge lives

**For building or continuing a project** (the common case): start at
[`framework/INDEX.md`](framework/INDEX.md). It is the actual entry point —
stage order, contracts, generators, deploy scripts, and the platform
constraints found by running this against real Fabric tenants. Load it before
touching a spec.

**For a specific hard-won platform behaviour**: [`framework/LEARNINGS.md`](framework/LEARNINGS.md)
is the incident log — DirectLake silently falling back to DirectQuery on a
view, SCD2 `valid_from` corrupting history if seeded with "today", workspace
folders being UI-only with no creation API, and others. Read it when
something fails in a way the error message doesn't explain.

**For the two written skills**: `skills/fabric/lakehouse-warehouse-topology.md`
and `skills/powerbi/direct-lake-and-tmdl.md`. Everything else under `skills/`
is an empty folder holding an `SKILLS_INDEX.md` that says so — that index is
the authority on what exists in its domain, not this file.

**For an existing project's status**: don't guess from the file tree —
```bash
python framework/tools/project_status.py --project <name>
```
Status is derived from the specs themselves (unfilled placeholders, failed
validation), so it can't drift from reality the way a written summary can.

## What this repository is not

Not a plugin marketplace, not an agent registry. `plugins/`, `agents/`,
`config/`, `scripts/`, `examples/`, and `mcp-setup/` exist as empty
directories — they were scaffolded for a broader vision that the actual build
effort went a different direction from. `docs/` holds real content, but only
`ARCHITECTURE.md`, `CI-SETUP.md`, and three architecture diagrams — not the
nested `getting-started/` / `tutorials/` / `advanced/` tree an earlier version
of this file described.
