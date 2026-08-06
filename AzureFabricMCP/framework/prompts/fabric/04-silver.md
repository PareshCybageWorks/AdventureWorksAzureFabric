# Stage F4 — Silver

**Track:** Fabric  **Stage:** 4 of 5  **Produces:** `fabric/04-silver.yaml`
**Contract:** `framework/contracts/fabric/04-silver.schema.json`  **Requires:** F3

---

## Purpose

Cleanse and conform. Silver is the first layer anyone should trust, and it holds
cleansed **staging** (`stg_*`) — not a dimensional model. History and surrogate
keys belong to gold.

## Two rules that govern everything

**1. Nothing is dropped silently.** Every rejecting rule names a quarantine
table. A row that vanishes without a reason recorded is a bug, not a cleansing
step. The layer identity must hold:

```
count(bronze) == count(silver) + count(quarantine)
```

**2. Every rule is a named library function.** `fn:` values come from
`ttfabric/cleansing.py` REGISTRY, and the contract's enum is generated from it —
so a typo fails at validation, and a rule that does not exist cannot be
referenced. The generator emits calls, never bespoke logic.

---

## Step 1 — The decision that matters: quarantine or correct?

For each defect from F2, choose deliberately. Getting this backwards is
expensive in both directions.

**Correct when the inputs are trustworthy and only a derived value drifted.**
`subtotal ≠ quantity × unit_price` means the stored total is stale; quantity and
unit price are fine. Recompute, keep the original in a companion column, raise a
flag. *Quarantining these would discard 2% of real revenue to punish an
arithmetic error.*

**Quarantine when the row cannot be trusted or repaired.** An order whose
customer was purged upstream, a refund written as a negative total, an order
with no date. These are quarantined rather than patched — and specifically **not**
routed to an "Unknown Customer" placeholder, because that makes a genuine
upstream deletion look like ordinary data and hides the count that should have
raised an alert.

## Step 2 — Order the rules

`rules:` execute top to bottom and the order is load-bearing:

```
cast_types          →  arithmetic needs real types
apply_transforms
deduplicate         →  BEFORE referential integrity, so a duplicate is
                       never counted as a broken reference
enforce_*           →  domain and range checks
recompute_*         →  AFTER the table it rolls up from is built
add_record_hash     →  last; gold uses it to detect real changes
```

`depends_on` declares cross-table order. A header that recomputes its total from
lines must run **after** those lines are corrected — rolling up uncorrected
lines propagates their error into the header and makes reconciliation pass
against wrong numbers.

## Step 3 — Business key, not surrogate id

`business_key` is what identifies the *thing*. For customers that is usually
email: a retried load re-delivers the same person under the same id, but a
genuine re-registration produces a new id for the same person. Deduplication
keys off this, so the wrong choice either merges distinct people or fails to
merge duplicates.

## Step 4 — Quarantine mode

`quarantine.write_mode` must be `overwrite`, matching silver's own mode. Silver
is fully rebuilt each run; an appended quarantine accumulates rejects from every
previous build and breaks the layer identity across runs.

## Step 5 — Cascade rejections to children

A row rejected here orphans its children in every table below. **Nothing
notices**: the child rows are valid in isolation, so no rule fires, and they
travel on until a join in a later layer discards them silently.

That is precisely how 999 quarantined orders left 2,945 of their lines behind,
which gold's inner join then dropped — putting ₹15,837,278 outside every report,
traceable only as a reconciliation gap nobody was measuring yet.

Declare it at **layer level**, not on a table:

```yaml
cascade_quarantine:
  - child: stg_order_items
    parent: stg_orders
    join_on: order_id
    reason: parent order failed cleansing
```

**It runs as a post-pass, after every table in the layer.** It cannot live in
the child's own build: a parent is often cleansed *after* its child — an order
header is corrected from its lines, so `stg_orders depends_on stg_order_items` —
and until the whole layer is built, which parents survived is not yet known.
The generator emits `nb_cascade_quarantine` and the pipeline runs it last.

State the **cause** in `reason` ("parent order failed cleansing"), not the
symptom a later layer would report ("no matching order_id").

> **This appends to the quarantine table, which Step 4 says must be
> `overwrite`.** Not a contradiction: cleansing overwrites it earlier in the
> same run, and the cascade appends to that fresh table afterwards. The layer
> identity holds because both happen within one run — an append that outlived
> the run would break it exactly as Step 4 describes.

## Adding a rule

1. Write the function in `ttfabric/cleansing.py`, returning `(kept, rejected)`
2. Register it in `REGISTRY`
3. `python framework/generators/sync_contract_rules.py`
4. Rebuild and publish the wheel

Skipping step 3 makes `validate.py` fail with an enum-drift error, by design.

## Exit gate

- [ ] `validate.py` passes, including `rule-enum` and `coverage`
- [ ] Every F2 `known_issues` id has a rule declaring `handles:`
- [ ] Every rejecting rule declares `on_reject`
- [ ] `count(bronze) == count(silver) + count(quarantine)` on a real run

Then proceed to **F5 — Gold**.
