# AzureFabricMCP — directory structure

## Corrected 2026-08-27

This file previously described a tree with `plugins/`, `agents/`, `config/`,
`scripts/`, `examples/`, `.claude-plugin/`, `.github/`, and a nested `docs/`
hierarchy — 100+ files that were never created. What follows is the actual
tree, generated from disk, not retyped from memory.

```
AzureFabricMCP/
├── README.md                Not present — the framework's overview lives one
│                             level up, at the repository root (see below)
├── CLAUDE.md                 What an AI assistant should load, and when
├── PROJECT_SUMMARY.md         Status snapshot: what's real, what's empty
├── STRUCTURE.md                This file
├── DRAWIO_USAGE_GUIDE.md      How to read/edit the architecture diagrams
├── Azure_Fabric_PowerBI_Architecture.drawio
│
├── framework/                THE REAL SYSTEM — reusable, no project data
│   ├── INDEX.md               Start here for any stage work
│   ├── LEARNINGS.md           Platform gotchas found by running this for real
│   ├── contracts/             JSON Schema per spec kind — AUTHORITATIVE
│   │   ├── fabric/             01-scaffolding, 02-sources, 03-bronze,
│   │   │                        04-silver, 05-gold  (5 schemas)
│   │   ├── powerbi/            01-semantic-model, 02-reports  (2)
│   │   ├── dataops/            01-monitoring, 02-audit  (2)
│   │   └── cicd/               01-pipeline  (1)
│   ├── prompts/                Per-stage interview procedure: ask, derive,
│   │   │                        validate, gate — mirrors contracts/ 1:1,
│   │   │                        plus:
│   │   └── 00-interview.md      sequences all ten stages as one conversation
│   ├── templates/              Blank specs to copy into a new project —
│   │                            mirrors contracts/ 1:1 (10 files)
│   ├── generators/             spec → artefact
│   │   ├── generate_notebooks.py
│   │   ├── generate_pipelines.py
│   │   ├── generate_ddl.py
│   │   ├── generate_tmdl.py
│   │   ├── generate_report.py       (PBIR)
│   │   ├── generate_monitoring.py
│   │   ├── generate_audit.py
│   │   ├── generate_workflows.py    (GitHub Actions)
│   │   ├── validate.py              schema + semantic checks
│   │   ├── validate_specs.py
│   │   ├── sync_contract_rules.py
│   │   └── _specs.py                shared spec-loading
│   ├── deploy/                  artefact → Fabric (REST + OneLake DFS)
│   │   ├── create_workspaces.py
│   │   ├── provision_storage.py
│   │   ├── push_library.py          the ttfabric wheel
│   │   ├── push_files.py
│   │   ├── push_items.py
│   │   ├── organise_items.py        files items into folders (folders must
│   │   │                            already exist — see LEARNINGS.md)
│   │   ├── push_semantic_model.py
│   │   ├── push_reports.py
│   │   ├── run_migrations.py        warehouse DDL
│   │   ├── connect_git.py           optional Fabric git mirroring
│   │   └── _project.py, _secrets.py, _tsql.py   shared
│   ├── tools/                  operational — status, dry-run, diagnose
│   │   ├── new_project.py
│   │   ├── project_status.py        ground truth for "is this done"
│   │   ├── dryrun.py                spec check against real data, no Fabric
│   │   ├── extract_rest.py          REST source → landing CSVs
│   │   ├── query_model.py, query_warehouse.py
│   │   ├── run_notebooks.py, diagnose_notebook.py, drop_tables.py
│   │   ├── verify_bindings.py, reset_layers.py, run_monitor.py
│   │   └── generate_sample_data.py, generate_netmon_data.py
│   ├── ttfabric/                PySpark runtime, shipped as a wheel
│   │   ├── cleansing.py           16 rules, (kept, rejected) — nothing
│   │   │                          dropped silently
│   │   ├── dimensions.py          SCD2 merge, point-in-time lookup
│   │   ├── quality.py             DQ run log + hard assertions
│   │   ├── warehouse.py           routes gold IO by storage.gold.write_mode
│   │   └── monitoring.py          9 expectation evaluators, Spark-free
│   ├── tests/                   runs without Spark or pytest
│   │   ├── test_monitoring.py
│   │   └── test_validator_catches.py   proves validate.py catches real defects
│   ├── pyproject.toml            builds the ttfabric wheel
│   └── requirements.txt
│
├── skills/                    2 files total — see skills/README.md
│   ├── README.md               honest at the top; a stale table further down
│   │                            still claims 15/12/10/10/8 — not yet fixed
│   ├── fabric/
│   │   ├── SKILLS_INDEX.md
│   │   └── lakehouse-warehouse-topology.md
│   ├── powerbi/
│   │   ├── SKILLS_INDEX.md
│   │   └── direct-lake-and-tmdl.md
│   ├── dataops/SKILLS_INDEX.md      folder otherwise empty
│   ├── dbt/SKILLS_INDEX.md          folder otherwise empty
│   ├── github-actions/SKILLS_INDEX.md   folder otherwise empty
│   └── common/SKILLS_INDEX.md       folder otherwise empty
│
├── docs/
│   ├── ARCHITECTURE.md
│   ├── CI-SETUP.md                accurate — Azure/GitHub prerequisites
│   ├── Azure_Fabric_PowerBI_Architecture.drawio
│   ├── AI_Master_Orchestrator_Architecture.drawio
│   ├── Orchestration_Flow_Simplified.drawio
│   └── azure_fabric_powerbi_medallion_architecture.png
│
├── plugins/        empty
├── agents/         empty
├── config/         empty
├── scripts/        empty
├── examples/       empty
└── mcp-setup/      empty
```

## Where the projects live

Not inside `AzureFabricMCP/` — it holds framework only, no project data. They
are sibling folders one level up, at the repository root:

```
Techtonic Agentic AI/           the outer (project-hosting) repository
├── README.md                    the accurate overview — read this first
├── AzureFabricMCP/                this tree, checked out as its own repo
├── 01_demo-project/               10/10 stages filled
├── 02_demo-servicenow/            0/10 — template only
├── 03_live_demo/                  10/10 stages filled
├── 04_PushkarDemo/                10/10, validated
└── 05_rahul_demo/                 6/10, in progress
```

Each project folder mirrors the same shape: `fabric/`, `powerbi/`, `dataops/`,
`cicd/` hold the filled specs; `generated/` holds generator output, committed
on purpose; `data/synthetic/` holds calibrated demo data with a defect
manifest.

## Finding things

- **Starting or resuming a project:** `framework/INDEX.md`
- **A behaviour that failed in a way the error didn't explain:** `framework/LEARNINGS.md`
- **Whether a project's specs are actually done:** `python framework/tools/project_status.py --project <name>`
- **What a spec kind's fields mean:** `framework/contracts/<track>/<stage>.schema.json`
  is authoritative; `framework/prompts/<track>/<stage>.md` explains the "why"
  a schema can't express
