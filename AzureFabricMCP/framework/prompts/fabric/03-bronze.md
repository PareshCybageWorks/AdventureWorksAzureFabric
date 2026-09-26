# Stage F3 — Bronze

**Track:** Fabric  **Stage:** 3 of 5  **Produces:** `fabric/03-bronze.yaml`
**Contract:** `framework/contracts/fabric/03-bronze.schema.json`  **Requires:** F2

---

## Purpose

Land the source, unchanged. Bronze exists to make ingestion replayable, so the
single rule is: **do not transform.**

## The one thing to get right

Bronze is `append` and immutable — the contract permits nothing else. That makes
the **watermark mandatory**, and the consequence of getting it wrong is the
nastiest failure in the whole framework:

> Without a watermark, every re-run re-appends the entire source. Silver then
> **deduplicates the copies away**, so the silver counts stay exactly correct
> while bronze silently multiplies. Nothing fails. The layer whose only job is
> to be a faithful record of what arrived quietly stops being one.

Observed in this framework: bronze reached 3–4× its true row count while every
silver table still reconciled perfectly.

## Step 1 — Derive, do not ask

Almost everything here comes from F2. Read it and emit:

| Field | From |
|---|---|
| `target` | `bronze_{source}_{entity}` |
| `source_entity` | the F2 entity |
| `load_pattern`, `watermark.column` | the F2 entity |
| `columns` | always `passthrough` |

Ask only when F2 left a `load_pattern: incremental` entity without a usable
watermark — and then go back and fix F2, not this stage.

## Step 2 — Resist the urge to filter

The contract permits only `columns: passthrough`. Selecting a subset here drops
upstream data that cannot be recovered without a re-extract. Exclusions belong
in silver, where the loss is reversible because bronze still has the row.

The same applies to "just this one obviously-bad row". Every defect declared in
F2 `known_issues` must survive into bronze intact. If bronze fixed anything, a
silver bug would be indistinguishable from an upstream change, and a replay
would not reproduce the original state.

## Step 3 — Landing checks only

Bronze checks whether the file **arrived**, never whether its contents are any
good:

- `header_matches_registry` — must read the header **separately**, without the
  declared schema. Reading it from the loaded DataFrame returns the schema's own
  names, so the check compares the schema to itself and can never fail — while
  the schema, applied positionally, has silently loaded values into the wrong
  columns.
- `row_count_not_zero` — a zero-row file is almost always a broken export
- `row_count_within_order_of_magnitude` — catches truncation and duplication

## Exit gate

- [ ] `validate.py` passes
- [ ] Every incremental table has a watermark that exists on its entity
- [ ] `columns: passthrough` everywhere
- [ ] Re-running the bronze pipeline loads **zero** new rows

That last one is the real test, and it is cheap: run `p_load_bronze` twice and
confirm the second run reports nothing new.

Then proceed to **F4 — Silver**.
