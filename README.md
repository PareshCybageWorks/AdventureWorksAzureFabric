# Azure Data & BI Integration

A spec-driven framework for building Microsoft Fabric and Power BI solutions.

You write specs. Contracts validate them, generators turn them into notebooks,
pipelines, warehouse DDL, semantic models, reports and CI workflows, and deploy
scripts push those to Fabric. Nothing in the pipeline is authored by hand twice.

---

## Layout

**The framework and projects live in separate repositories.** A project pins a
framework **tag**, so a framework change cannot break a project's build without
a commit in that project saying so.

| Repository | Holds |
|---|---|
| this one | the framework, tagged `v0.1.0` |
| [demoproject_azurefabricmcp](https://github.com/PareshCybageWorks/demoproject_azurefabricmcp) | the commerce demo project |

Clone them side by side; every path below resolves the same way locally as it
does in CI, where the framework is checked out to `AzureFabricMCP/`.

```
AzureFabricMCP/          the framework -- reusable, no project data ever
├── framework/
│   ├── contracts/       JSON Schema per spec kind -- AUTHORITATIVE
│   ├── prompts/         per-stage procedure: ask, derive, validate, gate
│   ├── templates/       blank specs to copy into a new project
│   ├── generators/      spec -> notebooks / pipelines / DDL / TMDL / PBIR / workflows
│   ├── deploy/          artefact -> Fabric (REST + OneLake DFS)
│   ├── ttfabric/        PySpark runtime, shipped as a wheel on the Spark Environment
│   ├── tools/           sample data, dry run, warehouse and model queries
│   └── tests/           runs without Spark or pytest
└── skills/              deep platform knowledge, read on demand

<project>/               a project, in its own repo -- authored design only
├── fabric/              F1-F5
├── powerbi/             P1-P2
├── dataops/             D1
├── cicd/                C1
├── generated/           produced by the generators; committed on purpose
└── data/synthetic/      calibrated demo data with a defect manifest
```

**Framework is reusable, projects hold design only.** Each project is its own
repository and pins a framework tag; none of them contain framework code, and
this repository contains no project data.

Start with [`AzureFabricMCP/framework/INDEX.md`](AzureFabricMCP/framework/INDEX.md)
— it maps the whole framework in one screen so you can open only what a task
needs.

---

## Stages

| Stage | Produces | |
|---|---|---|
| **F1** Scaffolding | workspaces, lakehouses, warehouse, Spark environment | ✅ |
| **F2** Sources | the source registry and its known issues | ✅ |
| **F3** Bronze | landing, watermarked and idempotent | ✅ |
| **F4** Silver | cleansing via 16 modular rules, with quarantine | ✅ |
| **F5** Gold | star schema, SCD2 dimensions, `bi` views | ✅ |
| **P1** Semantic model | TMDL, DirectLake, DAX measures | ✅ |
| **P2** Reports | PBIR pages and visuals | ✅ |
| **D1** DataOps | expectations, SLAs, per-environment enforcement | ✅ |
| **D2** Data audit | row and value flow bronze to gold, reported in Power BI | ✅ |
| **C1** CI/CD | GitHub Actions workflows and gates | ✅ |

---

## Quick start

```bash
pip install -r ../AzureFabricMCP/framework/requirements.txt
az login

# validate every spec against its contract (30 checks)
python ../AzureFabricMCP/framework/generators/validate.py --project .

# generate
python ../AzureFabricMCP/framework/generators/generate_notebooks.py --specs . --out generated/notebooks
python ../AzureFabricMCP/framework/generators/generate_tmdl.py      --specs . --out generated/model

# deploy
python ../AzureFabricMCP/framework/deploy/push_items.py           --project . --env dev --create-missing
python ../AzureFabricMCP/framework/deploy/push_semantic_model.py  --project . --env dev
python ../AzureFabricMCP/framework/deploy/push_reports.py         --project . --env dev

# prove it works -- deploying is not the same as working
python ../AzureFabricMCP/framework/tools/query_model.py --project . --smoke
```

---

## How it works

**Contracts are authoritative.** Every spec is validated against a JSON Schema,
then against semantic checks a schema cannot express — cross-spec references,
layer naming, dialect, threshold units. Validation fails the build.

Each semantic check exists because the mistake it catches actually happened:

- a DirectLake model bound to a view — which does not fail, it silently falls
  back to DirectQuery
- a measure colliding with a column of the same name
- a report field that no longer exists — visuals render **empty**, not broken
- a DQ threshold written as `2.2` meaning 2.2%, which can never fire
- a workflow step naming a script that does not exist

**Generation is deterministic.** Lineage tags and PBIR ids are UUID5-derived, so
regenerating is byte-identical and reports keep their bindings. The `no-drift`
CI gate re-runs every generator with `--check`, so a hand edit to a generated
file cannot survive a pull request.

**Environments are resolved at deploy time.** Generated artefacts carry
placeholders (`@@workspace@@`, `@@lakehouse:name@@`, `@@sqlendpoint@@`,
`@@modelid@@`) resolved against the target workspace, so one reviewed artefact
promotes from dev to prod unchanged and binds correctly on arrival.

---

## Status

Verified end to end against a real Fabric workspace: revenue reconciles from
silver to gold exactly, all 17 DAX measures evaluate, and the DQ monitor runs
across all three layers.

Proven on **one** project. The second will surface assumptions the first never
tested — most likely in `generate_notebooks.py`, which carries the most
demo-shaped logic. Worth expecting rather than being surprised by.

A project's CI needs repository secrets (`AZURE_CLIENT_ID`,
`AZURE_CLIENT_SECRET`, `AZURE_TENANT_ID`), GitHub Environments per branch, and
— while this repository is private — a PAT so the project can check the
framework out. See `docs/CI-SETUP.md`.

## Versioning

Projects pin a tag. `v0.1.0` is the first: ten stages with contract, prompt and
template, 30 validation checks, proven end to end on a real Fabric tenant across
two environments producing identical figures.

Tag a release when a contract changes shape. A project then upgrades by editing
one line in its `cicd/01-pipeline.yaml` — a reviewable change, rather than a
build that breaks because something moved in a branch nobody was watching.

## Branching

`feature/*` → `dev` → `qa` → `main` in a PROJECT repository, matching its four
Fabric workspaces. This repository has no deployment branches; it is a library.
