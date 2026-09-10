---
name: extend-fabric-workspace
description: Write a new notebook for an existing Microsoft Fabric workspace by first reading the code already there - lakehouse and warehouse tables, PySpark notebooks, pipelines - and matching its conventions. Use when adding a notebook or pipeline step to a workspace someone else built, extending an inherited Fabric solution, or when asked what existing notebooks do before changing anything.
---

# Extend an existing Fabric workspace

Write a notebook that fits the workspace it joins, instead of a second way of
doing what is already being done.

## Step 0 — Is this workspace spec-managed? Ask before anything else.

```bash
python AzureFabricMCP/framework/tools/project_status.py --project <project>
```

**If a project directory with specs exists, do NOT hand-write a notebook.**
Its notebooks are generated from `fabric/*.yaml`, and anything written by hand
will be overwritten by the next deploy and fail the `no-drift` CI gate. The
correct change is to the spec, then regenerate:

```bash
python AzureFabricMCP/framework/generators/generate_notebooks.py --specs <project> --out <project>/generated/notebooks
```

Use `/new-data-project` for that path. **Say this out loud to the user rather
than quietly doing the wrong one** — a hand-written notebook in a spec-managed
workspace looks like it works right up until the next deployment erases it.

The rest of this skill is for workspaces with **no spec behind them**: inherited
solutions, another team's work, a proof of concept someone else started.

## Step 1 — Read what is already there

```bash
python AzureFabricMCP/framework/tools/inspect_workspace.py --project <project> --env dev
python AzureFabricMCP/framework/tools/inspect_workspace.py --workspace <guid> --out ./inspected
```

Read-only. It fetches every notebook and pipeline definition and reports:

- **item inventory** by type
- **conventions in use**, counted across items — `spark.read.table` vs abfss
  paths, overwrite vs append, whether a parameters cell is standard
- **tables referenced**, and the naming prefixes in play
- with `--out`, the decoded definitions on disk so you can read them properly

**The convention counts are the point.** The portal shows one item at a time,
so you can read ten notebooks and never notice that nine use one access pattern
and the tenth uses another.

## Step 2 — Learn the shape, not just the syntax

Open two or three of the decoded notebooks — one that reads, one that writes,
one that is closest to your task. Establish:

- **How tables are addressed.** `spark.read.table("stg_orders")`, a fully
  qualified `[lh_silver].[dbo]`, or an abfss path. Mixing these in one
  workspace is how two people end up maintaining two conventions.
- **How the layers are named.** `bronze_*`, `stg_*`, `dim_*`/`fct_*` prefixes
  tell you where your output belongs and what it should be called.
- **How writes behave.** Overwrite, append, or merge — and whether a watermark
  or run id makes a re-run safe. Getting this wrong duplicates data on the
  second run, not the first, which is why it survives testing.
- **How parameters arrive.** A parameters cell, `mssparkutils`/`notebookutils`,
  or hard-coded values. A pipeline calling your notebook will pass them the way
  it passes them to the others.
- **What is imported.** A shared library (`ttfabric`, or the local equivalent)
  means helpers already exist for logging, cleansing and audit columns. Writing
  your own is duplication that then diverges.

## Step 3 — Check the data before you write against it

```bash
python AzureFabricMCP/framework/tools/query_warehouse.py --project <project> --sql "SELECT TOP 5 * FROM dbo.<table>"
```

Read-only. Confirm the columns and types you intend to use actually exist and
hold what you assume. **A notebook written against a remembered schema fails at
runtime with a message that names the column but not the reason.**

## Step 4 — Write the notebook

Match the majority convention from Step 1. Where you deliberately differ, put
the reason in a comment in the notebook — the next person will otherwise read
it as a mistake and "fix" it.

Carry over whatever the workspace already does for:

- **audit columns** (load timestamp, run id, source) if its tables have them
- **logging**, in the same form the others use, so one monitor sees all of it
- **idempotency** — re-running must not double the data

## Step 5 — Prove it runs, then prove it is right

```bash
python AzureFabricMCP/framework/tools/run_notebooks.py    --project <project> --env dev <notebook-name>
python AzureFabricMCP/framework/tools/diagnose_notebook.py --project <project> <notebook-name>
python AzureFabricMCP/framework/tools/query_warehouse.py  --project <project> --sql "SELECT COUNT(*) FROM ..."
```

**A failed Fabric notebook reports one sentence** — `System cancelled the Spark
session due to statement execution failures` — for a wrong column, a failed
assertion, a schema mismatch and a capacity eviction alike. Do not guess from
it. `diagnose_notebook.py` gets the real traceback; `run_notebooks.py` run alone
distinguishes a genuine defect from capacity contention.

Then check the numbers. **Running is not the same as correct** — a join on the
wrong key produces rows, just the wrong ones.

## Traps in an inherited workspace

**A minority convention is either a deliberate exception or a bug.** Find out
which before copying it. The inspect report flags patterns used by under a
third of items for exactly this reason.

**Lakehouse and warehouse are not interchangeable.** The Fabric warehouse Spark
connector is overwrite-only — an append pattern that works against a lakehouse
table silently does not transfer.

**Workspace folders are UI-only.** A new item lands at the workspace root and
must be filed by hand; it does not inherit a folder from anything.

**Nothing here creates a spec.** If this workspace should become spec-managed,
that is real work — writing the specs that describe it and confirming the
generated output matches — not a conversion step. Say so plainly rather than
implying the framework can adopt it automatically.
