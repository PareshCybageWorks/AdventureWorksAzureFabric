# Framework Index

Load this file first. It maps the framework in one screen so you can open only
what a task needs, rather than reading the tree.

**Framework = reusable.** Contracts, prompts, skills, templates, library, tools.
No project data ever lives here.
**Project = design only.** Filled-in specs under `<project>/{fabric,powerbi,dataops,cicd}/`.

> **Paths below are relative to the framework root** — the `AzureFabricMCP/`
> directory, not the repository root. Projects sit beside it, so from the
> repository root every command gains an `AzureFabricMCP/` prefix:
> `python AzureFabricMCP/framework/generators/validate.py --project 02_demo-servicenow`.

---

## Starting a new project

```bash
python framework/tools/new_project.py --name 02_your-project
```

Creates the folders, copies all ten stage templates, and writes a README with
the order to fill them in.

**To fill them in as a conversation, follow `prompts/00-interview.md`** — it
sequences the ten stages, and carries how to ask, what to derive rather than
ask, and what to refuse to guess. To find out where an unfinished project got
to, in any session:

```bash
python framework/tools/project_status.py --project 02_your-project
```

Status is derived from the specs themselves — a stage is unfinished if it still
holds placeholders or fails validation — so there is no progress file to fall
out of step with reality. **Validation fails immediately on a fresh project --
that is intended.** The templates carry `<placeholder>` values, and a validator
that passed on an unfilled template would be worthless on a filled one.

Fill the specs in dependency order; each reads names and types from the ones
above it:

```
F1 scaffolding -> F2 sources -> F3 bronze -> F4 silver -> F5 gold
                                     |
                        P1 model -> P2 reports
                        D1 monitoring, D2 audit
                        C1 ci/cd  (needs F1 environments)
```

Read `prompts/<track>/<stage>.md` before writing each spec. The prompts carry
the failure modes, not just the field list -- which is the part that saves time.

Re-run `validate.py` after each stage rather than at the end. It is the cheapest
gate here and catches the most.

## Layout

```
framework/
├── contracts/    JSON Schema per spec kind -- AUTHORITATIVE
├── prompts/      Per-stage agent procedure: ask, derive, validate, gate
├── templates/    Blank specs to copy into a new project
├── ttfabric/     PySpark runtime -- packaged as a wheel on the Spark Environment
├── pyproject.toml  builds the ttfabric wheel
├── generators/   spec -> notebooks / pipelines / DDL / TMDL / PBIR
├── tests/        verdict logic, runs without Spark or pytest
├── deploy/       artefact -> Fabric (REST + OneLake DFS)
└── tools/        sample data, dry run, REST extract, warehouse + model
                  queries, notebook run/diagnose/drop helpers
```

Skills live at the repo root in `../skills/<track>/`, not under `framework/`.

## Stages

| Stage | Produces | Contract | Prompt | Status |
|---|---|---|---|---|
| **F1** Scaffolding | `fabric/01-scaffolding.yaml` | ✅ | ✅ | done |
| **F2** Sources | `fabric/02-sources.yaml` | ✅ | ✅ | done |
| **F3** Bronze | `fabric/03-bronze.yaml` | ✅ | ✅ | done |
| **F4** Silver | `fabric/04-silver.yaml` | ✅ | ✅ | done |
| **F5** Gold | `fabric/05-gold.yaml` | ✅ | ✅ | done |
| **P1** Semantic Model | `powerbi/01-semantic-model.yaml` | ✅ | ✅ | done |
| **P2** Reports | `powerbi/02-reports.yaml` | ✅ | ✅ | done |
| **D1** Monitoring | `dataops/01-monitoring.yaml` | ✅ | ✅ | done |
| **C1** CI/CD | `cicd/01-pipeline.yaml` | ✅ | ✅ | done |

**All four tracks are complete.** Fabric, Power BI, DataOps and CI/CD each have
a contract, prompt, template and generator, and the demo project is migrated to
every one. `validate.py` runs 24 checks across all four; `ci-validate` passes
locally, including six drift checks proving every generated artefact matches its
spec.

**Fabric and Power BI are both complete and deployed.** `sm_commerce` runs
DirectLake over `wh_gold` and all 17 measures evaluate — Revenue reconciles to
gold exactly. `rpt_executive_overview` (2 pages, 17 visuals) is bound to it, and
every field reference is resolved against the model at validation.

**The Fabric track is complete.** All five stages have a contract, a prompt and
a template, and the demo project is fully migrated. Generators resolve through
`generators/_specs.py` and deploy scripts through `deploy/_project.py`, both of
which prefer the track layout and fall back to the legacy `specs/` set — so
either produces byte-identical output with the same commands.

## Commands

```bash
# validate (schema + semantic)
python framework/generators/validate.py --project <p> [--track fabric] [--strict]

# generate
python framework/generators/generate_notebooks.py --specs <p> --out <p>/generated/notebooks
python framework/generators/generate_pipelines.py --specs <p> --out <p>/generated/pipelines
python framework/generators/generate_ddl.py   --specs <p> --out <p>/generated/migrations
python framework/generators/generate_tmdl.py  --specs <p> --out <p>/generated/model
python framework/generators/generate_report.py --specs <p> --out <p>/generated/reports
python framework/generators/generate_monitoring.py --specs <p> --out <p>/generated/notebooks
python framework/generators/generate_workflows.py --specs <p> --out <repo-root>
python framework/generators/sync_contract_rules.py            # after adding a cleansing rule

# deploy
python framework/deploy/create_workspaces.py --project <p> --capacity <id>
python framework/deploy/push_library.py      --project <p> --env dev --wait
python framework/deploy/push_files.py        --project <p> --env dev
python framework/deploy/push_items.py        --project <p> --env dev [--create-missing]
python framework/deploy/organise_items.py    --project <p> --env dev
python framework/deploy/connect_git.py       --project <p> --env dev   # optional mirror

# test (no Spark, no pytest)
python framework/tests/test_monitoring.py
python framework/tests/test_validator_catches.py   # proves validate.py FAILS on real defects

# run without Fabric (fast spec check against real data)
python framework/tools/dryrun.py --project <p>

# extract a REST source into landing CSVs (spec-driven; full snapshot)
python framework/tools/extract_rest.py --project <p>

# when something fails in Fabric
python framework/tools/run_notebooks.py      --project <p> --env dev nb_x [nb_y ...]
python framework/tools/diagnose_notebook.py  --project <p> --env dev nb_x
python framework/tools/drop_tables.py        --project <p> --env dev --item wh_gold fct_x
```

**A failed notebook reports one sentence** — `System cancelled the Spark
session due to statement execution failures` — for a wrong column, a failed
assertion, a schema mismatch and a capacity eviction alike. Re-run it ALONE
first: if it passes, it was contention. If it fails, use
`diagnose_notebook.py`. See `LEARNINGS.md`.

## Runtime library

| Module | Provides |
|---|---|
| `ttfabric/cleansing.py` | 16 rules, each returning `(kept, rejected)` so nothing is dropped silently |
| `ttfabric/dimensions.py` | SCD2 merge, point-in-time key lookup, unknown member, calendar |
| `ttfabric/quality.py` | DQ run log + hard assertions |
| `ttfabric/warehouse.py` | Routes gold IO to Warehouse or Lakehouse per `storage.gold.write_mode` |
| `ttfabric/monitoring.py` | 9 expectation evaluators + verdict logic (Spark-free, tested) |

Delivered as a **wheel attached to the `env_spark` Environment**, so notebooks
`import ttfabric.cleansing` natively. Publishing takes ~4 minutes (Fabric
rebuilds the pool image), which is the real cost versus a file copy — but it
removes per-lakehouse duplication, puts the library under source control, and
ends the dependence on a notebook's default-lakehouse binding having propagated.

## Platform constraints worth knowing before you debug

These cost real time to discover. None announced itself as an error.

- **Warehouse connector: overwrite only.** `append` fails `Write orchestration failed` with no cause. Read, union, overwrite.
- **A warehouse infers NOT NULL from the first write.** Rows with unset columns are then rejected outright.
- **Item creation cannot set a folder.** Everything is born at the workspace root; `organise_items.py` files it afterwards.
- **A notebook's default lakehouse cannot be a Warehouse.** Gold notebooks default to the lakehouse they *read*, so an unqualified write lands in silver.
- **SCD2 `valid_from` must start at the beginning of time on first load.** Stamping "today" sends every historical fact to the unknown member — and `assert_not_null` still passes, because `-1` is not null.
- **A dimension cannot self-correct.** SCD2 preserves history including wrong history; fixing load logic needs `merge_scd2(rebuild=True)`.
- **Fabric reports only "session failed"** for a notebook run. To diagnose remotely, have the notebook write its own traceback to OneLake.
- **F2/F4 capacity cannot run Spark usefully.** It runs, slowly, with no error.
- **YAML 1.1 turns bare `on`/`off`/`yes`/`no` keys into booleans**, and an unquoted comma in flow style ends the value.
- **DirectLake cannot read a view.** Binding to one does not fail; it falls back to DirectQuery silently. Bind to `dbo.*`.
- **A measure and a column cannot share a name** — and only the first collision is reported, so they surface one deploy at a time.
- **TMDL `///` must sit directly above its object**, and a relationship has no description property at all.
- **Lineage tags must be deterministic**, or each deploy detaches every report from the model.
- **A deployed model is not a working model.** Verify with `query_model.py --smoke`.
- **A report field that no longer exists renders EMPTY, not broken.** Nothing logs it; `check_report_fields` resolves every reference against P1.
- **A generated workflow's step paths are relative to the REPO ROOT**, not the framework folder; a bad path fails only once CI is running.
- **Manual approval gates cannot be generated.** They are required reviewers on a GitHub Environment; a deploying job with no `environment` has no gate at all.
- **Cross-layer reads need per-layer resolution.** Bronze, silver and gold are three storage items of two kinds; an unqualified `spark.read.table` silently hits the default lakehouse and reports TABLE_OR_VIEW_NOT_FOUND, which reads as missing data.
- **`rule` is a reserved word in T-SQL.** A Delta column named `rule` writes fine from Spark and is unqueryable through the SQL endpoint.
- **DQ thresholds are fractions, count deltas are percent.** A share threshold above 1.0 can never fire, so the check looks like coverage while measuring nothing.
- **`report.json` requires `themeCollection`** even for the stock theme, and `reportVersionAtImport` is a string at schema 2.0.0, an object at 3.1.0.

## Conventions

```
bronze   bronze_{source}_{entity}     raw, provenance in the name
silver   stg_{entity}                 cleansed STAGING -- never dim_/fct_
gold     dim_{entity} / fct_{entity}  the dimensional model
notebook nb_{verb}_{entity}           verbs: load / clean / build
pipeline p_{verb}_{layer}
```

Silver is not named dimensionally on purpose: it overstates what it holds, and
puts `dim_customers` one character from gold's `dim_customer`.
