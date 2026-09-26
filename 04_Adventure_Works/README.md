# 04_Adventure_Works

Scaffolded from the TechTonic Fabric framework. **Every spec below is a
template and must be filled in** — validation fails until they are, which
is the intended starting state.

## Fill these in, in this order

The order is a dependency chain, not a preference: each stage reads names
and types from the ones above it.

| # | Spec | Depends on |
|---|---|---|
| 1 | `fabric/01-scaffolding.yaml` | Workspaces, capacity, topology, storage, naming. |
| 2 | `fabric/02-sources.yaml` | Where data comes from, and what is known to be wrong with it. |
| 3 | `fabric/03-bronze.yaml` | Landing. Needs F2 for entity names. |
| 4 | `fabric/04-silver.yaml` | Cleansing rules. Needs F3 for source tables. |
| 5 | `fabric/05-gold.yaml` | Star schema and reporting views. Needs F4. |
| 6 | `powerbi/01-semantic-model.yaml` | Model over gold. Needs F5 for column types. |
| 7 | `powerbi/02-reports.yaml` | Reports over the model. Needs P1 for field names. |
| 8 | `dataops/01-monitoring.yaml` | Expectations and enforcement. Needs F3-F5. |
| 9 | `dataops/02-audit.yaml` | Row and value flow bronze to gold. Needs F3-F5. |
| 10 | `cicd/01-pipeline.yaml` | Workflows and gates. Needs F1 for environments. |

Each has a procedure in `framework/prompts/<track>/<stage>.md` — read it
before filling the spec in. They carry the failure modes, not just the
field list.

## After each stage

```bash
python <framework>/generators/validate.py --project 04_Adventure_Works
```

Validation is authoritative. Every check in it exists because the mistake
it catches actually happened on a real project.

## Once the specs are filled in

```bash
# generate
python <framework>/generators/generate_notebooks.py --specs 04_Adventure_Works --out 04_Adventure_Works/generated/notebooks
python <framework>/generators/generate_pipelines.py --specs 04_Adventure_Works --out 04_Adventure_Works/generated/pipelines
python <framework>/generators/generate_ddl.py       --specs 04_Adventure_Works --out 04_Adventure_Works/generated/migrations
python <framework>/generators/generate_tmdl.py      --specs 04_Adventure_Works --out 04_Adventure_Works/generated/model
python <framework>/generators/generate_report.py    --specs 04_Adventure_Works --out 04_Adventure_Works/generated/reports

# provision and deploy
python <framework>/deploy/create_workspaces.py  --project 04_Adventure_Works --capacity <id>
python <framework>/deploy/provision_storage.py  --project 04_Adventure_Works --env dev
python <framework>/deploy/push_library.py       --project 04_Adventure_Works --env dev --wait
python <framework>/deploy/push_items.py         --project 04_Adventure_Works --env dev --create-missing
python <framework>/deploy/run_migrations.py     --project 04_Adventure_Works --env dev
python <framework>/deploy/push_semantic_model.py --project 04_Adventure_Works --env dev
python <framework>/deploy/push_reports.py       --project 04_Adventure_Works --env dev
python <framework>/deploy/organise_items.py     --project 04_Adventure_Works --env dev
```

`organise_items` runs **last**: it files only what exists when it runs, so
before the model and reports are deployed it would leave them at the
workspace root and report success.

## Then prove it works

```bash
python <framework>/tools/query_model.py     --project 04_Adventure_Works --smoke
python <framework>/tools/verify_bindings.py --project 04_Adventure_Works --env dev
```

Deploying is not the same as working. A model with a relationship on the
wrong column publishes cleanly and returns blank.
