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

## Tooling added while learning this

| Tool | Use |
|---|---|
| `tools/extract_rest.py` | REST → landing CSVs, spec-driven. Writes a FULL snapshot; bronze owns incrementality. |
| `tools/run_notebooks.py` | Run named notebooks, in batches or one at a time. |
| `tools/diagnose_notebook.py` | Get the real traceback out of a failing notebook. |
| `tools/drop_tables.py` | Targeted drop, for a schema change or a doubled table. |
| `tests/test_validator_catches.py` | Prove the validator fails on real defects. |
