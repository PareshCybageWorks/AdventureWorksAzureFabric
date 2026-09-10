# AzureFabricMCP — status

## Corrected 2026-08-27

This file previously claimed "55+ skills, 15,000+ lines, Production Ready"
across five workload domains, with a matching directory tree of `plugins/`,
`agents/`, `config/`, `scripts/`, `examples/`, and a nested `docs/` structure.
None of that content was ever written; the tree below is what is actually on
disk. See `CLAUDE.md` for why, and where the real work lives instead.

## What's real

**The framework** (`framework/`) — a spec-driven pipeline for Fabric + Power
BI, not a skill library. Ten stages, each with a JSON Schema contract, an
interview prompt, a blank template, and a generator that turns a filled spec
into notebooks, pipelines, warehouse DDL, TMDL, or PBIR:

| Track | Stages | Contracts | Prompts | Generators |
|---|---|---|---|---|
| Fabric | F1 scaffolding → F5 gold | 5 | 5 | 4 (notebooks, pipelines, DDL, workflows shared across tracks) |
| Power BI | P1 semantic model, P2 reports | 2 | 2 | 2 (TMDL, PBIR) |
| DataOps | D1 monitoring, D2 audit | 2 | 2 | 2 (monitoring, audit) |
| CI/CD | C1 pipeline | 1 | 1 | shared workflow generator |

Plus a deploy layer (13 scripts: workspace creation, item/library/file push,
git mirroring), an operational toolset (14 scripts: status, dry-run, REST
extraction, notebook diagnosis, warehouse/model queries), and `ttfabric` — a
PySpark runtime library (cleansing, SCD2, quality, warehouse routing,
monitoring) shipped as a wheel on the Spark environment, not copied
per-lakehouse.

Verified end to end against a real Fabric tenant: `sm_commerce` runs
DirectLake over `wh_gold`, all 17 measures evaluate, revenue reconciles from
silver to gold exactly. See `framework/LEARNINGS.md` for what that verification
actually cost — the platform behaviours that don't announce themselves as
errors.

**Skills** (`skills/`) — two files, written because a behaviour cost real
debugging time and the explanation didn't belong in a stage prompt:
`fabric/lakehouse-warehouse-topology.md` and `powerbi/direct-lake-and-tmdl.md`.
`dataops/`, `dbt/`, `github-actions/`, and `common/` are empty folders.

**Docs** (`docs/`) — `ARCHITECTURE.md`, `CI-SETUP.md`, and three architecture
diagrams (two `.drawio`, one `.png`). `CI-SETUP.md` is accurate and current:
what has to exist in Azure/GitHub before deploy workflows run.

## What's empty

`plugins/`, `agents/`, `config/`, `scripts/`, `examples/`, `mcp-setup/` — zero
files in every one. No `.github/` workflows, no `.claude-plugin/` manifest,
despite both being described in the file this replaces.

## The five projects built on this framework

Sibling folders to `AzureFabricMCP/`, one repository each in production, all
using `framework/tools/project_status.py` for ground truth rather than a
written summary:

| Project | Stages filled | Domain |
|---|---|---|
| `01_demo-project` | 10/10 | commerce demo |
| `02_demo-servicenow` | 0/10 — template only | ServiceNow (superseded by 03?) |
| `03_live_demo` | 10/10 | ServiceNow ITSM, time tracking, infra KPIs |
| `04_PushkarDemo` | 10/10, validated (33 passed, 0 errors, 3 deliberate warnings) | Kastle workplace occupancy, local Postgres |
| `05_rahul_demo` | 6/10 | in progress |

## Versioning

Projects pin a framework tag (`v0.1.0` is current) in their CI pipeline, so a
framework change cannot break a project's build without a commit in that
project saying so. See the top-level `README.md` for the two-repository split
this implies, and why `AzureFabricMCP` must be the checkout directory name.
