# P1 — Semantic Model

Produce `<project>/powerbi/01-semantic-model.yaml`: the tabular model every
report binds to.

Contract: `framework/contracts/powerbi/01-semantic-model.schema.json` — authoritative.
Template: `framework/templates/powerbi/01-semantic-model.yaml`.

**Depends on F5 Gold.** The model describes what gold produced; it cannot be
written first. Column types are read from `fabric/05-gold.yaml`, so a column
missing there becomes a string in the model — and a revenue column typed as
string is a model with no `SUM`, which nothing reports as an error.

---

## Ask the user

1. **Which gold tables belong in the model?** Not all of them. A staging or
   audit table in the model is a table a report author will eventually use.
2. **What is each measure's business definition?** Especially where two exist:
   billed vs recognised revenue, gross vs net. Capture the distinction in the
   measure `description` — that text is what a user sees in the field list, and
   it is the only place the definition reaches them.
3. **Is row-level security needed?** If yes, on which column, and driven by
   what — a region column, a user-mapping table?
4. **Which currency and locale?** It sets `culture` and every format string.

Do not invent measures the user did not ask for. A model with 40 speculative
measures is harder to use than one with 12 that were requested.

---

## Derive

- **One table per gold entity**, named in business language. `dim_customer`
  becomes `Customer`; the warehouse name never reaches a report author.
- **Hide every raw numeric column on a fact.** Report authors aggregate through
  measures. A visible `Line Revenue` column lets someone build a total that no
  measure defines and no one reviewed.
- **Name fact columns `Line ...`** — `Line Revenue`, `Line Cost`. It reads
  correctly at the grain *and* keeps them clear of the measure names, which is
  not optional (see below).
- **Surrogate keys are not declared.** The generator injects any column a
  relationship or `key` needs, hidden. Listing them is noise.
- **Exactly one table with `kind: date`.**

---

## Gate — refuse to proceed if

- **A measure and a column on the same table share a name.** The model refuses
  to load, and reports only the first collision, so they surface one deploy at
  a time. `validate.py` finds them all at once.
- **`storage_mode: direct_lake` and any `source_table` is not a physical table.**
  DirectLake reads delta files. A view has none. This does not fail — it falls
  back to DirectQuery, and every report is slower for a reason that appears
  nowhere. Bind to `dbo.*`; the `bi` views remain for SQL clients and paginated
  reports, which have no such restriction.
- **A hierarchy level or `sort_by` names a source column instead of a display
  name.** The model loads and the sort is silently absent — months order April,
  August, December.
- **A dimension has no `key`.**
- **`cross_filter: both` without a stated reason.** Bidirectional filtering
  creates ambiguous paths that make totals wrong without making them fail.

---

## Then

```bash
python AzureFabricMCP/framework/generators/validate.py      --project <p> --track powerbi
python AzureFabricMCP/framework/generators/generate_tmdl.py --specs   <p> --out <p>/generated/model
python AzureFabricMCP/framework/deploy/push_semantic_model.py --project <p> --env dev
python AzureFabricMCP/framework/tools/query_model.py        --project <p> --smoke
```

`query_model.py --smoke` evaluates every measure. **Run it.** A model that
deploys is not a model that works: a relationship on the wrong column, or a
partition over an empty table, publishes cleanly and returns blank.

Blank is legitimate for a period comparison with no prior period. Blank
everywhere is not.
