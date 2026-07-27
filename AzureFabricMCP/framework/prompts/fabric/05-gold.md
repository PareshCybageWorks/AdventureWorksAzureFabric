# Stage F5 — Gold

**Track:** Fabric  **Stage:** 5 of 5  **Produces:** `fabric/05-gold.yaml`
**Contract:** `framework/contracts/fabric/05-gold.schema.json`  **Requires:** F4

---

## Purpose

The star schema and the reporting views — what Power BI connects to, and the
only layer a business user should see.

`dbo` holds physical tables written by the gold notebooks. `bi` holds views
shaped for reporting. No pipeline reads from `bi`; it exists so a report can be
reshaped by altering a view instead of migrating a table.

---

## This is where a warehouse looks correct and reports wrong numbers

Everything below is here because it happened, and because **none of it produced
an error**.

### SCD2 `valid_from` on the first load

A member's first version must be valid from the beginning of time. Stamping
"today" on an initial load means a point-in-time lookup —
`event_date BETWEEN valid_from AND valid_to` — matches **nothing** for any fact
older than the load, which on a historical backfill is all of them. Every fact
falls through to the unknown member.

The warehouse then reports **100% of revenue against "Unknown Customer"** while
every not-null assertion passes, because `-1` is not null.

### A dimension cannot self-correct

SCD2 preserves history — including history recorded wrongly. Unchanged rows
carry their bad `valid_from` through every subsequent run, so fixing the load
logic does **not** fix an already-built dimension. It needs
`merge_scd2(rebuild=True)`, which is explicitly destructive and never a default.

### `assert_not_null` cannot detect a broken lookup

A failed lookup deliberately yields the unknown-member key so the row survives.
So a fact table with *every* key unresolved passes a not-null check. Use
`assert_keys_resolve`, which fails when more than ~1% fall through.

### Four names, not one

A dimension key involves four names that only collapse in the easy case:

| | customer | date |
|---|---|---|
| fact's join column (`lookup_on`) | `customer_id` | `order_date_key` |
| dimension's business key | `customer_id` | `full_date` |
| dimension's surrogate key | `customer_sk` | `date_sk` |
| column written on the fact (`target`) | `customer_sk` | `order_date_sk` |

Treating them as one concept survives until a date dimension appears. It
surfaced three separate times here before all four were separated.

---

## Step 1 — Declare the grain in words

The contract requires it and length-checks it. *A fact whose grain nobody wrote
down is a fact somebody will double-count.* Then assert it: `unique(...)` on the
degenerate dimension that defines it.

## Step 2 — Choose tracked columns deliberately

`scd2_tracked_columns` decides what opens a new version. A phone correction
should **not** fabricate a historical event; a region move **must**, because it
changes which territory the sales are attributed to. Track too much and history
becomes unreadable within a month.

## Step 3 — Give every dimension an unknown member

It guarantees an unmatched fact still joins rather than vanishing from a report
without trace. Its `defaults` must cover **every** non-nullable column — a
warehouse infers NOT NULL from the first write and rejects a member carrying
nulls in columns the spec did not name.

A dimension without one is worse than useless: unmatched facts resolve to `-1`,
which does not exist, and the rows disappear from any report joining it.

## Step 4 — Flag provisional measures

A measure standing in for data the project does not have — a flat cost ratio
where no cost column exists — must set `provisional: true`. The generator then
marks it visibly in the notebook, so it cannot quietly inform a pricing
decision.

## Step 5 — Reconcile back to silver

Gold must tie out. Compare against the rows that actually **reached** gold: an
inner join legitimately drops lines whose header was quarantined, so comparing
against all of silver fails a correct build. Reconcile on **money**, not on
whatever additive measure comes first — a check that ties out unit counts while
revenue drifts is worse than no check.

## Exit gate

- [ ] `validate.py` passes
- [ ] Every fact declares its grain and asserts it
- [ ] Every dimension has an unknown member with complete defaults
- [ ] `assert_keys_resolve` passes — no mass fallback to `-1`
- [ ] Gold revenue reconciles to silver within tolerance
- [ ] Provisional measures are flagged

Fabric track complete. Proceed to the **Power BI** track.
