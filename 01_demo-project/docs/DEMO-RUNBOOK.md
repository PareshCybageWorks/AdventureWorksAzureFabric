# Demo runbook — Executive, 10–15 minutes

Live against real Fabric and Power BI.

**The claim being demonstrated:** the pipeline is not built by clicking. It is
declared in specs, generated, validated, deployed and promoted — and it can
prove what it did to the data.

**The one line to land:** *every number on this report can be traced back to the
row it came from, and the pipeline tells you what it discarded.*

---

## T-30 minutes — before recording

Run these in order. Each is quick except where noted.

```bash
cd "D:/CDAI/TechTonic/AgenticAI/Techtonic Agentic AI"
```

**1. Confirm Fabric is healthy.** This is not optional — OneLake was
unavailable for ~40 minutes during the build, and every upload fails when it is.

```bash
python AzureFabricMCP/framework/tools/query_warehouse.py --project 01_demo-project --env dev --smoke
```

Expect row counts and four `bi` views. If it hangs for 45 seconds, OneLake is
degraded — **do not start recording**. Check the Fabric admin portal's service
health and wait.

**2. Reset the layers, so the run you record starts from empty.**

```bash
python AzureFabricMCP/framework/tools/reset_layers.py --project 01_demo-project --env dev
```

**Use this, not "delete the workspace".** Deleting reserves every item name for
minutes, and rebuilding then fails on all of them with an error that reads like
a naming conflict. `reset_layers` empties `Tables/` and leaves the items and the
landed source files alone.

**3. Warm the Spark pool.** A cold session adds 2–3 minutes to the first
notebook and it will happen on camera.

```bash
python AzureFabricMCP/framework/tools/run_monitor.py --project 01_demo-project --env dev --skip-run
```

**4. Open these tabs, in this order:**

| # | Tab |
|---|---|
| 1 | `01_demo-project/fabric/05-gold.yaml` in the editor |
| 2 | A terminal, at the repo root |
| 3 | Fabric → `AgenticAIDemo_dev` → `4_master/pipeline/p_orchestrate_master` |
| 4 | [Executive Overview report](https://app.powerbi.com/groups/13c508c8-8fe8-4539-9cf4-2b4a3b91b32f/reports/b096b10e-241b-44c4-b179-f5275ee6c1e7) |
| 5 | Fabric → `AgenticAIDemo_qa` (to show promotion) |

**5. Capacity.** Running on the **FTL64 trial**. It is ample — the full run
takes ~13 minutes on it. The risk is not speed, it is expiry: when the trial
lapses every environment loses compute at once, including prod. Nothing to do
before the demo; worth saying if asked.

---

## The recording

The full pipeline takes **~13 minutes**, which is the whole demo slot. So it is
started early and talked over. That is not a workaround — it is the honest shape
of the thing, and the wait is where the specs get explained.

### 0:00–1:30 — Start the run, state the problem

Tab 3. Start `p_orchestrate_master`. Then leave it running.

> "That is now loading four source files, cleansing them, building a star
> schema and refreshing a Power BI model. It takes about thirteen minutes. While
> it runs, I want to show you what it is running *from* — because nobody
> clicked anything to build it."

### 1:30–4:00 — The spec is the product

Tab 1, `fabric/05-gold.yaml`. Scroll to the `fct_sales` join.

Point at `on_unmatched: quarantine` and read the comment aloud.

> "This is a spec, not code. It says: build a fact table, join to the order
> header, and if a line has no header, do not drop it silently — quarantine it
> so somebody can count it.
>
> That rule exists because it bit us. An inner join was discarding 2,945 order
> lines worth ₹15.8 million, and every report was 4% short with nothing anywhere
> saying so."

Then Tab 2:

```bash
python AzureFabricMCP/framework/generators/generate_notebooks.py --specs 01_demo-project --out 01_demo-project/generated/notebooks
```

> "Fourteen notebooks, regenerated from those specs in under a second. Nobody
> edits these. If you edit one by hand, CI fails."

### 4:00–6:30 — Validation is the interesting part

```bash
python AzureFabricMCP/framework/generators/validate.py --project 01_demo-project
```

29 checks pass. Then say what a few of them are for:

> "These aren't style checks. Each one is a mistake that actually happened.
>
> This one" — `semantic-model` — "stops a Power BI model binding to a view,
> which doesn't fail; it silently gets slower forever.
>
> This one" — `report-fields` — "catches a renamed measure. A report bound to a
> field that no longer exists doesn't break. It renders **empty**, and someone
> finds out in a board meeting.
>
> And this one" — `discarded-rows` — "refuses a spec where a join can throw rows
> away without saying what happens to them."

**Optional, if it is going well** — break something live:

```bash
python AzureFabricMCP/framework/generators/validate.py --project 01_demo-project --track powerbi
```

(Rename a measure in `powerbi/01-semantic-model.yaml` first, re-run, show the
error naming the exact visuals affected, then undo.)

### 6:30–9:00 — Promotion

Tab 5, the qa workspace. Same folder structure, same 26 items.

> "This is a different environment, built from the same specs — not copied.
> Every notebook here points at qa's own lakehouse, and we verify that rather
> than assume it."

```bash
python AzureFabricMCP/framework/tools/verify_bindings.py --project 01_demo-project --env qa --compare dev
```

> "Twenty items, none of them referencing dev. A notebook pointing at the wrong
> environment's data does not error — it just quietly reads and writes the wrong
> place."

### 9:00–11:00 — Back to the run

Tab 3. The pipeline should be in gold or finishing.

Walk the activity graph: bronze → silver → gold → views → audit.

> "Note the last two steps. The reporting views are built *after* the tables
> exist, because Spark creates those tables. And then it audits itself."

### 11:00–14:00 — The report

Tab 4. Three pages.

**Performance** — the business answer:

| | |
|---|---|
| Revenue | ₹365,491,581 |
| Recognised revenue | ₹282,613,762 |
| Gross margin | 29.4% |
| Orders | 23,955 |

> "Revenue and recognised revenue differ by ₹83 million. That is not an error —
> it is orders billed but not yet delivered. The pipeline knows the difference
> because the spec defines it once, and every report inherits it."

**Data Quality** — 1,428 corrected lines, 2.0%.

> "Silver recomputed 1,428 subtotals that disagreed with quantity × price. The
> correction rate is on the executive report on purpose. A pipeline that quietly
> corrects data looks exactly like one that quietly corrupts it."

**Data Audit** — the closer. The cards read **95.2% of rows** and **94.5% of
value** retained to gold:

| entity | bronze | silver | gold |
|---|---|---|---|
| order_items | 74,782 | 71,130 | 71,130 |
| orders | 25,646 | 23,999 | — |
| customers | 2,048 | 1,971 | 1,972 |

> "74,782 lines arrived. 71,130 reached the star schema. The 3,652 difference is
> not missing — it is quarantined, counted, and queryable, because 1,647 orders
> failed cleansing and their lines went with them.
>
> Silver and gold agree to the paisa. That is the number I would want if I were
> signing off these figures."

### 14:00–15:00 — Close

> "Four source files to a governed star schema and a Power BI model. Every
> artefact generated from a spec, every spec validated, promoted across
> environments without editing anything, and the pipeline can account for every
> row it did not carry forward."

---

## If something goes wrong

The failures actually seen while building this, and what to do on camera.

| Symptom | Cause | Do this |
|---|---|---|
| A notebook hangs at "Running" for minutes | Cold Spark session | Keep talking. It resolves. This is why T-30 step 3 exists. |
| Upload or read times out ~45s | OneLake degraded | Stop. It is not recoverable in the moment. |
| `Invalid object name` on a table you can see | SQL endpoint lags the Spark write by a minute or two | Wait, re-run the query. |
| `ItemDisplayNameNotAvailableYet` | A deleted name is still reserved | Wait. Do not rename. |
| HTTP 429 `RequestBlocked` | Throttled | The scripts retry. Wait it out. |
| A pipeline activity fails | Genuine | Fabric only says "session failed" — open the notebook's own output, which prints the real error. |

**If the live run fails outright:** qa is fully populated with identical
numbers. Switch to the qa workspace and continue from section 9:00. Say so
plainly — a demo that recovers honestly is more convincing than one that never
stumbles.

---

## Numbers worth having memorised

| | |
|---|---|
| Source rows | 2,048 customers · 300 products · 25,646 orders · 74,782 lines |
| Revenue | ₹365,491,581.50 |
| Recognised revenue | ₹282,613,761.89 |
| Corrected subtotals | 1,428 (2.0%) |
| Quarantined lines | 3,652 |
| Orders failing cleansing | 1,647 |
| Full run | ~13 min from empty |
| Validation checks | 29 |
| Injected defects | 11 kinds, calibrated |
| Rows retained to gold | 95.2% |
| Value retained to gold | 94.5% |

Retention compares like for like — only entities that HAVE a gold layer.
Dividing by all of bronze gives 71%, because `orders` is a header table consumed
into the fact and was never going to appear in gold. If someone asks why 71%
appears anywhere, that is the answer.

Revenue is **identical in dev and qa**. If asked whether the demo data is
special: it is synthetic and seeded, with 11 defect types injected deliberately
so the cleansing has something real to do.
