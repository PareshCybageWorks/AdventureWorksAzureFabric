---
name: document-semantic-model
description: Document a Microsoft Fabric or Power BI semantic model - tables, columns, measures, relationships, lineage and the reasoning behind them - as a generated page that cannot drift from the model. Also verifies the deployed model actually returns numbers. Use when asked to document a semantic model or dataset, explain what a measure means or where a number comes from, hand a model over to another team, or check that documentation still matches the model.
---

# Document a semantic model

Produce documentation that stays true, and prove the model it describes
actually works.

## The rule: generate it, never write it

Model documentation is the first artefact to go stale. A measure is renamed, a
relationship re-pointed, a column dropped — and the page keeps asserting what
used to be true with nothing to signal otherwise. **A confidently wrong page is
worse than no page**, because people stop checking the model.

So do not hand-write this. Generate it from the same spec the model is
generated from:

```bash
python AzureFabricMCP/framework/generators/generate_model_docs.py \
    --specs <project> --out <project>/docs
```

This writes `<project>/docs/SEMANTIC-MODEL.md` covering the model overview,
every table and column, all measures grouped by display folder with their DAX,
relationships, deliberately-unrelated tables, and lineage back through gold.

**Add `--check` to CI.** It fails when the committed page no longer matches the
spec, which is what makes the page trustworthy rather than decorative:

```bash
python AzureFabricMCP/framework/generators/generate_model_docs.py \
    --specs <project> --out <project>/docs --check
```

## Then prove the model works

Documentation describes intent. It does not establish that the model returns
anything. **A relationship on the wrong column publishes cleanly and returns
blank** — the page would describe that model perfectly and still mislead.

```bash
python AzureFabricMCP/framework/tools/query_model.py     --project <project> --smoke
python AzureFabricMCP/framework/tools/verify_bindings.py --project <project> --env dev
```

`--smoke` evaluates every measure at total level, the fastest way to find the
one returning blank. Record the result alongside the page: documentation with
no evidence the model evaluates is half a deliverable.

## What to add by hand — and where

The generator emits structure and the reasoning already captured in the spec.
Two things it cannot know:

- **Why a measure is defined the way it is.** If `Orders` is a `DISTINCTCOUNT`
  because one order has many lines, that belongs in the spec's `description`
  field for that measure — *not* appended to the generated page, where the next
  regeneration erases it.
- **Who owns the model and who to ask.** That belongs in the project README.

**Every improvement to this documentation is an edit to
`powerbi/01-semantic-model.yaml`, then a regeneration.** If you find yourself
wanting to edit the Markdown, the description belongs in the spec.

## Documenting a model this framework did not build

For a `.pbix` or `.pbip` on disk, or a model with no spec behind it, this
generator does not apply — there is no spec to read. Use the
`powerbi-documentation` skill instead, which reads the model itself over an MCP
connection.

If the model lives in a Fabric workspace and you want it under this framework,
the honest path is to write the P1 spec that describes it, confirm the
generated model matches, and document from there. That is real work, not a
conversion step — say so rather than implying otherwise.

## What good looks like

- [ ] `SEMANTIC-MODEL.md` generated, committed, and `--check` passing
- [ ] Every measure carries a `description` explaining what it means to the
      business, not what the DAX does — the DAX is already on the page
- [ ] Any table related to nothing is explained, since an unrelated table is
      usually a defect and occasionally deliberate
- [ ] `--smoke` run, with the result recorded
- [ ] The storage mode is stated, and for DirectLake the page carries the
      warning that it binds to tables and never to views

## Traps worth knowing

**DirectLake binding to a view** does not fail. It falls back to DirectQuery
silently and gets slower. The page flags this whenever the model declares
DirectLake.

**A measure and a column sharing a name** breaks TMDL, and the error names the
file rather than the collision.

**Types are not restated here.** They are derived from gold at generation time;
a type written in two places is a type that will eventually disagree.

**An audit table should be related to nothing.** It describes the pipeline, not
the business. Joining it to a dimension lets someone slice an audit by a filter
that was already applied and read the subset as the whole.
