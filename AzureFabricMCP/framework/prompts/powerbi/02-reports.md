# P2 — Reports

Produce `<project>/powerbi/02-reports.yaml`: the reports built over the P1 model.

Contract: `framework/contracts/powerbi/02-reports.schema.json` — authoritative.
Template: `framework/templates/powerbi/02-reports.yaml`.

**Depends on P1.** Every field is a reference into the semantic model, resolved
at validation. Whether a reference is a column or a measure is never declared
here — the model already knows, and restating it is a second place to be wrong.

---

## Ask the user

1. **Who reads this report, and what decision does it support?** Record it in
   `audience`. A report without a stated audience accumulates visuals until it
   answers nothing in particular.
2. **What are the three questions the page must answer?** Three is usually the
   limit for one screen. More than that is a second page.
3. **Should data quality be visible to this audience?** Often it should. A
   pipeline that silently corrects data looks exactly like one that silently
   corrupts it; the correction rate next to the affected numbers is what tells
   them apart.

---

## Derive

- **Layout is a reading order.** Headline numbers top-left, trend next,
  breakdown below. Positions are explicit pixels on a 1280×720 page; leave
  ~20px gutters.
- **Use measures, not columns, for anything numeric.** The model hides raw fact
  columns precisely so a report cannot invent its own aggregate.
- **Sort by the measure that matters**, descending, on any ranked visual.
  Unsorted category charts default to alphabetical, which buries the finding.
- **One slicer beats five.** Each one is a decision the reader has to make
  before seeing anything.

---

## Gate — refuse to proceed if

- **A field does not resolve against the P1 model.** This is the failure that
  makes reports untrustworthy: a renamed measure does not break its visuals, it
  empties them. Nothing logs it. `validate.py` resolves all of them.
- **A visual has no fields.** It renders as an empty box.
- **The report binds to a model this project does not define.**
- **Visuals overlap** unless deliberately layered — check positions.

---

## Then

```bash
python AzureFabricMCP/framework/generators/validate.py        --project <p> --track powerbi
python AzureFabricMCP/framework/generators/generate_report.py --specs   <p> --out <p>/generated/reports
python AzureFabricMCP/framework/deploy/push_semantic_model.py --project <p> --env dev   # P1 first
python AzureFabricMCP/framework/deploy/push_reports.py        --project <p> --env dev
```

**Deploy the model before the report.** `push_reports.py` refuses otherwise: a
report bound to a missing model publishes and renders nothing, which is
indistinguishable from a data problem.

The generated PBIR holds `@@modelid@@`, resolved at deploy time against the
model in the target workspace — so one reviewed artefact promotes across
environments and binds correctly on arrival.

Then open the report. Field references resolving is not the same as a page
being readable, and no validator has an opinion about layout.

---

## PBIR facts that cost a deploy each

- `report.json` **requires** `themeCollection`, even for the stock theme — and
  the theme it names must also appear in `resourcePackages`.
- `reportVersionAtImport` is a **string** at schema 2.0.0. The per-part object
  form belongs to 3.1.0 and is rejected.
- Page and visual ids must be **deterministic**. Random ids make every deploy
  look like a rewritten report and discard per-visual state.
