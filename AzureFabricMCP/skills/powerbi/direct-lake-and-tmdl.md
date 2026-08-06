# DirectLake binding and TMDL authoring

What a generated Fabric semantic model gets wrong, and why none of it announces
itself. Every entry here cost at least one deploy round-trip.

---

## DirectLake cannot read a view

DirectLake reads the delta files behind a table. A view has none.

Binding a DirectLake model to `bi.vw_sales_summary` **does not fail**. The model
publishes, the reports work, and every query silently falls back to DirectQuery
— slower, with no message anywhere saying so.

So the model binds to `dbo.dim_*` and `dbo.fct_*`, and the `bi` views serve SQL
clients, paginated reports and ad-hoc analysis, which have no such restriction.

This is the one place where the usual advice — "bind reports to views, never to
tables" — is wrong on this platform. The indirection that a view would provide
is supplied instead by the semantic model itself: measures, display names,
hidden columns and hierarchies all reshape the data without a view.

`validate.py` fails the build on this, because nothing downstream will.

---

## A measure and a column cannot share a name

```
The 'Recognised Revenue' measure cannot be created because a column
with the same name already exists.
```

The model refuses to load, and reports **only the first collision** — so with
three collisions you deploy three times.

Convention that avoids it entirely: fact columns are named `Line ...`
(`Line Revenue`, `Line Cost`, `Line Recognised Revenue`) and measures take the
business name (`Revenue`, `Cost`, `Recognised Revenue`). The prefix is accurate
at the fact's grain, so it is not a workaround.

Hide the raw columns as well. A visible numeric column on a fact lets a report
author drag it onto a visual and produce a total no measure defines.
`discourageImplicitMeasures` in the model discourages it; hiding prevents it.

---

## TMDL is strict in ways the error messages hide

- **`///` is a description property, not a comment.** It must sit on the line
  *directly* above its object. A blank line between them fails the whole file
  with `Unexpected line type: Empty!`, naming a line number but not the cause.

- **Not every object has a Description.** A relationship does not. `///` above
  one fails with `Property 'description' is unknown` — and a plain `//` comment
  in that position fails as `Invalid indentation`. Relationship documentation
  belongs in the spec, not the generated TMDL.

- **References use display names, not source columns.** A relationship pointing
  at `customer_sk` when the column is displayed as `Customer Key` fails with
  `refers to an object which cannot be found` — naming the *relationship*, so
  the message points at the wrong object entirely.

- **`sortByColumn` naming a source column does not fail.** The model loads and
  the sort is simply absent, which surfaces as months ordering April, August,
  December.

- **Indentation is tabs.**

---

## Lineage tags must be deterministic

Every TMDL object carries a `lineageTag` GUID. Reports bind to those GUIDs.

Generating fresh GUIDs each run rebuilds the model as far as Power BI is
concerned and **detaches every report** from it — the reports do not error, they
lose their fields.

`generate_tmdl.py` derives each tag as a UUID5 of the model name and object
path, so regeneration is byte-identical and bindings survive. The namespace UUID
is a constant for the same reason: changing it re-tags everything.

---

## Marking the date table

`kind: date` emits `dataCategory: Time` plus `isKey` on the date column. Without
it, time intelligence functions **return wrong answers rather than failing** —
`SAMEPERIODLASTYEAR` quietly returns the wrong window.

Verify per period, not at the grand total:

```
EVALUATE SUMMARIZECOLUMNS('Date'[Year], "Rev", [Revenue], "LY", [Revenue LY])
```

Each year's `LY` must equal the previous year's `Rev`. At the grand total,
`Revenue LY` legitimately equals `Revenue` (the shifted window still covers
every fact date) and `TOTALYTD` is legitimately blank (no single year in
context) — so a total-level check proves nothing either way.

---

## Deploying, and proving it works

```bash
python framework/deploy/push_semantic_model.py --project <p> --env dev
python framework/tools/query_model.py          --project <p> --smoke
```

`push_semantic_model.py` resolves `@@sqlendpoint@@` and `@@database@@` against
the target workspace, so one reviewed artefact promotes across environments
unchanged.

**A model that deploys is not a model that works.** A relationship on the wrong
column, or a partition over an empty table, publishes cleanly and returns blank.
`--smoke` evaluates every measure in one scan and lists the blank ones.
