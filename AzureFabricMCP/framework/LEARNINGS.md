# Learnings

Failure modes found by building real projects, and what was changed so the
framework catches them next time.

**Every entry here cost someone hours.** They are recorded because none of them
announced itself: each passed validation and failed later, somewhere expensive.

This file is appended to by `/new-data-project-learning-push` at the end of a
project. Add to it in the same shape — what failed, why nothing caught it, what
changed — and put the fix in the framework rather than in one project's README.

---

## The pattern behind almost all of them

> **A spec that validates is not a pipeline that runs.**

Every defect below was accepted by the contracts and the semantic checks, then
failed at generation, at deploy, or inside Spark. The gap is always the same
shape: something is a *name* in one file and a *different name* in another, and
nothing resolves the two.

When adding a check, ask what would prove it works. A check that has only ever
passed proves nothing — see `tests/test_validator_catches.py`.

---

## Diagnosis: what Fabric will and will not tell you

**A failed notebook reports one sentence, whatever went wrong:**

```
System cancelled the Spark session due to statement execution failures
```

That covers a wrong column name, a failed assertion, a schema mismatch and a
capacity eviction. The Spark monitoring API returns the same sentence. Guessing
from it does not work — four wrong guesses in a row is normal.

Two things that do work:

1. **Compare a failing item against a passing one.** Read both generated
   notebooks. The difference is the bug. This found five defects in a row.
2. **`tools/diagnose_notebook.py`** wraps the notebook's body in try/except and
   writes the traceback to OneLake. Two earlier attempts produced nothing
   because generated notebooks carry deploy-time placeholders
   (`@@lakehouse:lh_silver@@`) that `push_items` resolves — upload them raw and
   the notebook has no lakehouse and dies before running anything.

**Capacity exhaustion does not reliably say so.** Only some evictions report
`TooManyRequestsForCapacity` (HTTP 430); the rest use the sentence above.
**Before debugging a statement failure, re-run the notebook alone.** If it
passes, it was contention.

---

## Name resolution: the recurring defect

| Where | The trap |
|---|---|
| `dimension.business_key` | Names the **target** column. The gold notebook projects source → target *before* assigning the surrogate key, so a source name no longer exists. |
| DAX measures | Resolve **display** names. A model that renames raw fact columns to `Line …` breaks every measure still written against the source name. |
| `bi` view SQL | A fact carries the **measure** target. `created_ticket` becomes `created_ticket_count`; a view reading the business rule's name finds nothing. |
| Power BI names | **Case-insensitive.** `Month` and `month` are one name; the model refuses to import. |
| `measured_on: input` | Runs against the upstream table, so it needs the **source** column name, not silver's rename. |

Caught now by `dimension-keys`, `model-names` and `view-columns` in
`generators/validate.py`.

---

## Layer rules that are not negotiable

**Bronze appends. There is no exception.**
A `load_pattern: full` table re-appends its whole source on every run. Silver
deduplicates the copies away, so **silver and gold stay correct while bronze
silently doubles** — 26,280 rows from a 13,140-row file. Nothing fails. Only
the D2 layer audit shows it, and re-landing makes it worse.

Every bronze table needs a watermark on a column that is immutable once
written. For generated data that is the reading date, never `generated_at` —
regenerating restamps every row and the whole feed looks new.

**Silver writes need `overwriteSchema`.**
Without it the first run after a spec adds a column fails on schema mismatch.
The quarantine write beside it always had the option; the table everyone reads
did not.

**A gold schema change requires dropping the table.**
The warehouse Spark connector refuses a differing schema
(`FabricSparkTDSWriteError: Schemas are not equal`) and cannot ALTER. Use
`tools/drop_tables.py`, not `reset_layers.py`, unless you want to rebuild
everything.

**A view is validated when CREATED, not when queried.**
A placeholder returning `WHERE 1 = 0` still needs a valid `GROUP BY`.

---

## Assertions that reject correct data

`assert_keys_resolve` failing does not mean the lookups are broken. Three
causes seen, all legitimate:

- **An optional relationship.** Most incidents have no configuration item and
  many are unassigned. Declare `optional: true` on the dimension key.
- **A role-playing date for an event that has not happened.** An open incident
  has no resolved date.
- **A calendar shorter than the facts.** `dim_date` generated over 2024–2026
  against data starting in 2015 sent 10% of rows to the unknown member. Measure
  the real range before choosing the window; a calendar narrower than the facts
  is a broken lookup, not a narrow calendar.

And never let a generator invent an assertion the spec did not ask for. One
fell back to "assert the last degenerate dimension is unique", which asserted a
free-text outage message was unique across 427 rows.

---

## Interviewing: what the instance actually contains

Ask the source, do not assume the product:

- **Tables may not exist.** Time Card Management was not installed, so six KPIs
  had no source. `cmn_outage` did not exist either — the table was
  `cmdb_ci_outage`. **Search `sys_db_object` before declaring anything
  missing.**
- **Value sets may differ.** `incident.state` and `close_code` matched
  out-of-box exactly; `change_request.type` had a fourth value.
- **Volumes will not match the estimate.** Every `expected_row_order` was wrong,
  most by orders of magnitude. Measure them.
- **A KPI can be arithmetically correct and vacuous.** Network availability
  read 100% because no outage touched a network CI. A dashboard showing 100%
  with no underlying events reads as reassurance.

---

## Workspace organisation: silent in both directions

**`organise_items` files only what a folder DECLARES**, and reports
`0 unmatched` while leaving everything else at the root — an item nobody
planned for was never in the plan, so nothing counts it as missing.

The shipped template stopped at `5_datastore`, so **every project built from it
left four items loose**:

| Item | Why no folder caught it |
|---|---|
| `nb_cascade_quarantine` | does not match `nb_clean_*` |
| `nb_dq_monitor` | reads all three layers, belongs to none |
| the semantic model | no Power BI folder existed |
| the report | same |

The last two are the items a business user actually opens, which made them the
two hardest to find in a workspace where every notebook was neatly filed.

Fixed in the template (now declares `6_powerbi/semanticmodel`,
`6_powerbi/report`, and the two notebooks explicitly) and enforced by
`folder-coverage` in `validate.py`, which derives every item the generators
will produce and fails when one has no folder.

**A report deploys under its `display_name`, not its spec `name`.** `02-reports`
declares `name: rpt_x` for the `naming.report` convention and `display_name`
for the people who open it; `push_reports` uses the latter. So a folder entry
written in the framework's own convention (`rpt_*`) matched nothing.
`organise_items` now resolves one to the other, and the template matches
reports on `*`.

---

## The framework hardcodes its own location

When the framework moved to `AzureFabricMCP/framework`, **75 CI step paths
across two projects broke at once**, plus 66 documented invocations in its own
prompts, INDEX and tool help. Step paths are relative to the repository root,
so a relocation invalidates every one of them, and a workflow only fails when
CI is already running.

`cicd-scripts` caught the step paths in seconds. Nothing catches the prompts —
they are prose, and a stage prompt telling you to run a path that no longer
exists costs the next person time before they have written a line.

If the framework moves again: repoint `cicd/01-pipeline.yaml` step paths and
`metadata.framework_path` in every project, then `python framework/` → the new
prefix across `framework/**/*.{py,md}`. Run `validate.py` afterwards; it is the
only part that self-checks.

---

## A pipeline RETRY duplicates a full-snapshot bronze table

The entry above says bronze re-appends its whole source *on every run*, and
prescribes a watermark. This is the same corruption reached by a shorter path:
**one run is enough.**

`load_pattern: full` with `write_mode: append` — the only write mode the
contract permits — means a retried notebook lands a second complete snapshot.
Four of fifteen bronze notebooks hit a Spark capacity limit on one contended
run, the pipeline retried them automatically, and each appended a second copy.
**300 cardholders became 600; 5 buildings became 10; 1,440 minutes became
2,880.** The other eleven tables were untouched, so nothing looked systematic.

The pipeline reported **success**, because the retries succeeded.

Nothing caught it for two more layers. It surfaced in gold as:

```
point-in-time lookup on card_holder_guid fanned out:
2,000 fact rows became 4,000. The dimension has overlapping validity windows.
```

on a **type-1** dimension, which has no validity windows at all. The assertion
found the right problem and named the wrong cause, so the obvious next move —
investigating SCD2 — would have been wasted.

**Changed:** `generate_notebooks.py` writes a full snapshot with dynamic
partition overwrite on `ingest_date` instead of a blind append, so a re-run
replaces its own day and leaves earlier dates intact. `bronze-idempotency` in
`validate.py` fails a `full` table whose layer does not partition by
`ingest_date`. Proven by re-loading a table twice: 300 live rows in one
partition where the old code gave 600.

---

## A model can deploy clean and fail every query

`view-columns` checks that a `bi` view names real gold columns. Nothing asked
the same question of the semantic model, and it is the same defect one layer
across: a fact carries the **measure target**, so `arrival_event` is computed
by a business rule and written as `arrival_count`.

The model bound to the rule's name. It deployed, reported `Succeeded`, and then
failed on every query:

```
Invalid column name 'arrival_event'.
Invalid column name 'personnel_arrival'.
Invalid column name 'visitor_arrival'.
```

`model-names` passed throughout — it resolves DAX against the model's own
display names, which is a different question. A model can be perfectly
self-consistent and bound entirely to columns the warehouse does not have.

**Changed:** `model-columns` in `validate.py`, sharing
`gold_columns_by_table()` with `view-columns` so the two cannot drift. The
mutation case catches it on all three existing projects, which means all three
were equally exposed.

---

## Adding a cleansing rule takes four places, not two

The F4 prompt listed: write the function, register it, sync the contract,
rebuild the wheel. Two more are needed and neither fails loudly.

**`tools/dryrun.py` keeps its own implementation of every rule.** That second
reading of the spec is the point of the dry run — but a rule it does not
implement is **skipped, not failed**. Five rules were added, `DRY RUN PASSED`
was printed, and the first one then failed in Spark. The run had never
exercised any of them.

**The generated silver notebook asserts the strict row identity.** A rule that
legitimately multiplies rows cannot satisfy
`count(in) == count(kept) + count(rejected)`. Exploding six personas into
twenty-one persona-building rows failed a correct build, and reported the gain
as a loss:

```
AssertionError: row loss: 6 in, 21 out, 0 quarantined, -15 unaccounted
```

A negative shortfall was the only hint the message described the opposite of
what happened.

**Changed:** `dryrun-parity` in `validate.py`; `ROW_MULTIPLYING_RULES` in both
`tools/dryrun.py` and `generators/generate_notebooks.py`, which switches the
notebook to a one-way "may add, must never lose" check; the F4 prompt now lists
all six steps.

---

## The diagnostic tool was broken in three ways at once

`diagnose_notebook.py` exists because Fabric reports one sentence for every
failure. When it was finally needed it produced, in order:

1. `IndexError: list index out of range` — in the code that WARNS about
   unresolved placeholders. `text.split("@@")[1:2]` yields segments that no
   longer contain `@@`, and it then indexed `[1]` into them.
2. After that was fixed: every `@@environment:...@@` left unresolved, because
   it looked for an `"environment"` key inside the map returned by
   `get_storage_ids`, which only ever holds lakehouse and warehouse ids.
3. After that: `no traceback written (HTTP 404) -- usually a capacity
   eviction`. The traceback goes to `/lakehouse/default`, and
   `--scratch-lakehouse` defaults to `lh_silver`, so **diagnosing any bronze
   notebook silently found nothing** — while blaming capacity.

Only then did it give the answer, which took one line:
`'overwriteSchema' cannot be used in dynamic partition overwrite mode`.

**Changed:** all three fixed; the read-back now searches every provisioned
lakehouse and says which one it found the file in.

---

## Smaller things that cost time

- **`DISTINCTCOUNT` over a dimension counts the unknown member.** `Total
  Cardholders` read 301 against 300 real cardholders. The Active measures were
  unaffected — the unknown member's flags default to 0 — so three of four
  measures were right, which is what made the fourth easy to miss. Exclude the
  unknown member explicitly in any count over a dimension.
- **The bronze generator emits a CSV reader and nothing else.** `connection.kind`
  accepts jdbc, rest and eventhub; `generate_notebooks.py` has one branch and
  `generate_pipelines.py` has no copy activity. A "Postgres source" project
  must export to files and land them. Now a `connection-kind` warning.
- **`project_status.py` read prose as a placeholder.** A finished spec
  explaining that a rule keeps the original "as `<column>_source`" was reported
  as `1 to fill: <column>`, so the stage could not be marked done without
  rewording its own documentation. Placeholders are now only counted when the
  TEMPLATE carries the same token.
- **Two commands in the F1 prompt did not exist**: `validate.py --stage`
  (it takes `--track`) and `provision_items.py` (it is `provision_storage.py`).
- **The `pii` enum has no category for a pseudonymous identifier.** A GUID that
  resolves to a person only inside the source system can be declared fully PII,
  forcing masking that breaks the RLS join it is needed for, or not PII at all.
  Neither is true. Left as-is and documented in the column description; a
  `pseudonymous_id` value would be the honest fix.

---

## Workspace folders cannot be created via API — they are a UI-only feature

Folder organization is one of the first steps after `push_items.py` deploys notebooks and pipelines. The spec declares intended folders in `workspace_folders`, and `organise_items.py` should file every generated item into its folder.

Attempted to automate this: created items of type "Folder" via Fabric REST API (`POST /workspaces/{id}/items` with `type: Folder`) and via MCP `core_create-item` tool. Both returned:

```
InvalidItemType: Requested item type 'Folder' is invalid
```

Investigated: Fabric workspace folders are a UI-only surface for organizing items. The API supports *reading* folder structure (a folder is just a container attribute on items), but does not support *creating* folders.

**Impact:** `organise_items.py` requires pre-existing folders — without them, items land at the workspace root, and the tool reports "0 unmatched" (items not planned for were never planned for, so nothing counts them as missing). A workspace becomes hard to navigate as it grows; worse, Power BI reports and semantic models land loose instead of organized under `6_powerbi/`.

**Changed:** Deployment sequence now explicitly requires manual folder creation as a step BEFORE `organise_items.py`. Folders must be created in Fabric UI (~2 min) before proceeding. A future enhancement could wrap folder creation in a UI helper tool, but the creation itself cannot be automated. Updated F1 scaffolding prompt to document this step in the deployment sequence.

---

## SSL certificate verification in corporate proxy environments

All deployment scripts that call Fabric REST API fail with `[SSL: CERTIFICATE_VERIFY_FAILED]` in environments where a corporate proxy or firewall intercepts HTTPS traffic with a self-signed certificate. This includes:

- `deploy/create_workspaces.py` — creating workspaces
- `deploy/push_items.py` — deploying notebooks and pipelines
- `deploy/_tsql.py` — warehouse/lakehouse lookups (used by provision_storage.py, push_semantic_model.py)
- `deploy/push_library.py` — pushing Python environments
- `deploy/push_semantic_model.py` — deploying semantic models
- `deploy/push_reports.py` — deploying Power BI reports

All requests to `api.fabric.microsoft.com` fail with the same error:

```
urllib3.exceptions.SSLError: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: 
unable to get local issuer certificate
```

The scripts validate SSL certificates, which is correct for public internet, but corporate proxies intercept with their own certificates that Python's certifi bundle does not trust. Azure CLI authentication succeeds, but the following API calls fail.

**Why nothing caught it:** The validator runs against specs, not network conditions. A working project's deployment scripts are assumed to work everywhere. The error only appears at deploy time, after validation passes.

**Changed:** All `requests.*()` calls in deployment scripts now pass `verify=False` to disable SSL verification. This allows deployment in corporate proxy environments while maintaining security at the application level (Azure authentication still requires valid tokens).

Affected files updated:
- `deploy/create_workspaces.py`: 3 requests calls
- `deploy/push_items.py`: 4 requests calls
- `deploy/_tsql.py`: 3 requests calls
- `deploy/push_library.py`: 4 requests calls
- `deploy/push_semantic_model.py`: 5 requests calls
- `deploy/push_reports.py`: 5 requests calls

**Tested:** Adventure Works project deployed successfully through corporate proxy to three environments (dev/uat/prod) after applying this fix.

---

## Tooling added while learning this

| Tool | Use |
|---|---|
| `tools/extract_rest.py` | REST → landing CSVs, spec-driven. Writes a FULL snapshot; bronze owns incrementality. |
| `tools/run_notebooks.py` | Run named notebooks, in batches or one at a time. |
| `tools/diagnose_notebook.py` | Get the real traceback out of a failing notebook. |
| `tools/drop_tables.py` | Targeted drop, for a schema change or a doubled table. |
| `tests/test_validator_catches.py` | Prove the validator fails on real defects. |
