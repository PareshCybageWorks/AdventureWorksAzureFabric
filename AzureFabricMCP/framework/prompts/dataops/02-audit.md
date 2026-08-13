# D2 — Data Audit

Produce `<project>/dataops/02-audit.yaml`: how many rows and how much value
survive each layer, written to the warehouse so Power BI can report on it.

Contract: `framework/contracts/dataops/02-audit.schema.json` — authoritative.
Template: `framework/templates/dataops/02-audit.yaml`.

**Depends on F3–F5** for the table names at each layer, and on **P1** if the
audit is to be reported rather than only queried.

---

## What this answers that D1 does not

D1 asks *"is this table healthy"* — nulls, ranges, freshness, thresholds.

D2 asks *"where did the rows go"*. Different question, and the one nobody can
answer at 5pm when a total looks low. On this project it had to be answered by
hand three times: 2,945 order lines lost in a join, 999 orders quarantined
without their children, gold running 4.15% short of silver. Each needed a
different ad-hoc query and each was found only because somebody went looking.

D2 makes it a table.

---

## Ask the user

1. **Which entities matter?** Usually the value-bearing ones. A dimension that
   never loses rows is not interesting; the fact and its parents are.
2. **What is the same thing called at each layer?** That mapping is the whole
   spec — `bronze_commerce_order_items`, `stg_order_items` and `dbo.fct_sales`
   are one entity measured three times.
3. **What is the money column at each layer?** It changes name across layers
   (`subtotal` → `subtotal` → `line_revenue`) and that is exactly why it must be
   declared rather than guessed.
4. **Where does each layer put its rejects?** Without the quarantine table, a
   drop is unexplained; with it, the arithmetic closes.

---

## Derive

- **Always declare a measure where one exists.** A row count alone hides value
  moving without rows moving — a corrected subtotal changes the total while
  every row survives.
- **`grain_changes: true` on SCD2 entities.** A dimension holds one row per
  *version*, so gold legitimately exceeds silver. Without the flag the audit
  reports growth as a fault.
- **`filter: is_current = true`** for the current slice of an SCD2 dimension,
  where the comparable count is what you want.
- **An entity that ends at silver is fine** — declare only the layers it has.
  `orders` is a header table consumed into the fact and never appears in gold.

---

## Gate — refuse to proceed if

- **Fewer than two layers on an entity.** There is nothing to compare.
- **A money column exists and is not declared.** The audit then reports row
  counts and misses value loss entirely, which is the failure it exists for.
- **An SCD2 entity has no `grain_changes`.** It will report a correct build as
  a defect, and a check that cries wolf gets ignored.
- **The target is a lakehouse.** It must be the warehouse: the semantic model
  reads DirectLake from there, and an audit written anywhere else cannot be
  reported beside the numbers it audits.

---

## Then

```bash
python AzureFabricMCP/framework/generators/validate.py       --project <p> --track dataops
python AzureFabricMCP/framework/generators/generate_audit.py --specs   <p> --out <p>/generated/notebooks
python AzureFabricMCP/framework/deploy/push_items.py         --project <p> --env dev --create-missing
```

`nb_build_audit` runs last in `p_build_gold`, after the tables and views it
measures.

---

## Reporting it — the trap

Adding the audit table to P1 is where this goes wrong, in two ways that both
produce plausible numbers rather than errors.

**The table appends one row set per run.** A plain `SUM` therefore reports the
pipeline as having processed its own history over again. Measures must scope to
the newest run:

```
VAR latest = CALCULATE(MAX(Audit[Run]), ALLSELECTED(Audit))
RETURN CALCULATE(SUM(Audit[Row Count]), Audit[Run] = latest)
```

**A card has no axis.** `[Rows]` and `[Retention %]` are correct on a visual
carrying a layer and meaningless without one — on a card they sum bronze, silver
and gold together. Retention read **2.7%** that way, which looks like
catastrophic loss rather than a missing filter. Cards need layer-scoped
measures that name the layer themselves.

**And retention must compare like for like.** Dividing gold by *all* of bronze
gave **71%**, because an entity that legitimately has no gold layer sits in the
denominator with nothing above it. Restrict to entities that reach gold; the
honest answer was **95%**.

All three would have been read aloud to executives as evidence of data loss.
Relate the audit table to **nothing** — it describes the pipeline, not the
business, and joining it to a dimension lets someone audit a filtered subset
while it looks like the whole.
