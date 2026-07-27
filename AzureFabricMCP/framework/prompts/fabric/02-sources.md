# Stage F2 — Sources

**Track:** Fabric  **Stage:** 2 of 5  **Produces:** `fabric/02-sources.yaml`
**Contract:** `framework/contracts/fabric/02-sources.schema.json` (authoritative)
**Requires:** F1 complete

---

## Purpose

Declare the contract with upstream: every system, entity, column, and known
defect. This is the only place a source column is declared, and everything
downstream is checked against it.

## Preconditions

- [ ] F1 passed its exit gate
- [ ] Sample extracts exist, or the upstream schema is documented
- [ ] Someone upstream can answer questions about defects

---

## Step 1 — Ask

**1. What delivers the data, and how?**

System type and connection kind. If it is a file drop, get the actual file —
declared schemas and real files disagree more often than anyone expects, and
Step 3 checks that.

**2. What is the business key of each entity?**

Not the surrogate id — the key that identifies the *thing*. For customers it is
usually email, not `customer_id`: a retried load re-delivers the same person
under the same id, but a genuine re-registration produces a new id for the same
person. Deduplication keys off this, so getting it wrong either merges distinct
people or fails to merge duplicates.

**3. Which column carries the watermark?**

Required for `load_pattern: incremental`, and the contract enforces it. Without
one, bronze re-appends the entire source on every run — and downstream
deduplication *hides* it, so the counts stay plausible while bronze silently
stops being a faithful record of what arrived.

Prefer an update timestamp. Use a creation timestamp only when rows are
genuinely immutable once written.

**4. Which columns are PII?**

Classify at the source. `require_pii_classification` fails the build if a PII
column reaches silver neither masked nor dropped, and the classification is
what drives the masking policy.

**5. What is known to be wrong with this data?**

The most valuable question in the stage, and the one people skip. Ask directly:

> "What does this system get wrong? Duplicates? Nulls where there shouldn't be?
> Totals that don't add up? Orphaned references?"

Record each as a `known_issues` entry with an id and an observed rate. Every id
must be handled by a silver rule, and the validator proves it. A defect nobody
wrote down becomes a silent correction nobody can explain later.

---

## Step 2 — Measure, do not estimate

`observed_rate` must be measured. Data-quality thresholds are calibrated from
it — warn at roughly 1.5×, fail at roughly 2.5× — so a guessed rate produces a
threshold that either never fires or always fires. Both are useless; the second
gets muted within a week.

Profile the extract, or run `tools/dryrun.py` against it and read the counts.

## Step 3 — Verify column order against a real file

The bronze notebook applies this schema **positionally**. A mismatch does not
fail — it loads values into the wrong columns, and stays invisible whenever the
mismatched columns share a type. Two string columns swapped is silent, total
data corruption.

```bash
head -1 <file>.csv        # compare against the declared column order
```

## Step 4 — Validate

```bash
python framework/generators/validate.py --project <project> --track fabric
```

## Authoring trap: unquoted commas

In YAML flow style an unquoted comma ends the value:

```yaml
# WRONG -- description truncates at the comma; the rest becomes a null key
- { name: customer_id, type: string, description: Natural key, format "CUST_n" }

# RIGHT
- { name: customer_id, type: string, description: "Natural key, format CUST_n" }
```

The contract sets `additionalProperties: false` on columns specifically to turn
this into a build failure. It was found in a spec that had been passing for
weeks with half its documentation silently discarded.

---

## Exit gate

- [ ] `validate.py` passes with zero errors
- [ ] Every entity declares a `primary_key` and, if incremental, a watermark
- [ ] Column order matches a real extract
- [ ] Every PII column is classified
- [ ] `known_issues` rates are measured, not estimated
- [ ] Every `known_issues` id has a `handled_by` target

Then proceed to **F3 — Bronze**.

---

## Related

- `framework/prompts/fabric/01-scaffolding.md` — previous stage
- `framework/tools/generate_sample_data.py` — emits a defect manifest to calibrate from
- `framework/tools/dryrun.py` — measures real rates without touching Fabric
