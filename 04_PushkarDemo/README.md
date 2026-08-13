# 04_PushkarDemo

Executive workplace-occupancy dashboards for CxO, built on the Kastle
access-control data mart in the local PostgreSQL `kastle` database.

All ten specs are filled and validate: **33 passed, 0 errors, 3 warnings**
(each warning deliberate — see *Open items*).

```bash
python AzureFabricMCP/framework/tools/project_status.py --project 04_PushkarDemo
python AzureFabricMCP/framework/generators/validate.py  --project 04_PushkarDemo
python AzureFabricMCP/framework/tools/dryrun.py         --project ./04_PushkarDemo
```

---

## The brief, as answered

No written brief existed, so the five opening questions were asked directly.

| Question | Answer |
|---|---|
| What decision does this enable? | Executive dashboards — whether the office footprint matches actual use |
| Which sources, who owns each? | Local PostgreSQL `kastle` on `localhost:5432`, owned by data-engineering |
| Who reads the output, in what? | CxO, in Power BI |
| How fresh? | Full load, daily |
| What is known to be wrong? | *"No idea"* — so the database was profiled directly and eight defects were measured |

## What the data actually is

Kastle Systems building access control: badge reads at five Manhattan office
towers. 16 tables, 9.8 MB, PostgreSQL 18.3.

- **2,000 arrivals**, 2025-11-03 → 2026-02-12
- **300 cardholders**, identified by a 36-char Kastle GUID
- **5 buildings**, **30 companies**, **20 readers**
- 1,696 Personnel / 304 Visitor
- Referential integrity is **perfect** — zero orphans, zero null FKs on all
  five fact-to-dimension paths. Unusual, and treated as a property of a
  synthetic demo database rather than something to rely on.

## The eight measured defects

Every rate measured against the live database on 2026-08-10, never estimated.

| Id | Defect | Rate | Handled by |
|---|---|---|---|
| CAL-001 | Date dimension spans only 104 days, ending at the last fact date. Power BI time intelligence returns **blank, not an error** — every year-over-year tile renders empty and reads as a data gap | 1.0 | `extend_calendar` |
| DUP-001 | Same person re-enters the same building the same day. **Real events, not duplicates** — a row count over-reports headcount by ~4% | 0.0415 | `assert_grain` (records, never removes) |
| CON-001 | `is_active_in_last_365_days` is 1 for all 300 cardholders. The 30/60/90 flags do vary, so three of four options behave and the fourth silently does not | 1.0 | `recompute_activity_flags` |
| STA-001 | `date_range` is a relative-window label frozen at extract time, used as an averaging denominator | 1.0 | `apply_transforms` |
| NUL-001 | RLS scope null for 4 of 6 personas. Under-scoping shows as missing rows, not an error | 0.667 | `default_nulls` |
| JSN-001 | Scope held as JSON arrays in text columns; a star schema cannot join on an array | 1.0 | `explode_json_array` |
| KEY-001 | Enterprise↔institution bridge carries only one side | 1.0 | `flag_incomplete_reference` |
| NAM-001 | `buildingkey` / `readerkey` against `building_key` / `reader_key` everywhere else | 1.0 | `apply_transforms` |

`kastle_users` is **deliberately unregistered**: it holds `password_hash`,
email and full name for six demo logins and has no analytical value. An
unregistered entity cannot reach bronze, so exclusion at F2 is the control —
not a later masking rule.

## Decisions taken, and their cost

- **Medallion, not mesh.** One team, one source database, one cadence.
- **Ingestion is CSV, not JDBC.** Not a preference: `generate_notebooks.py`
  emits a `spark.read…csv()` reader for every bronze entity and has no JDBC
  branch, and `generate_pipelines.py` has no copy activity. PostgreSQL is
  exported to `data/kastle_pg/*.csv` so that what runs and what the spec says
  are the same thing.
- **Cardholder natural key is the GUID**, not the integer surrogate — the
  integer can be reassigned if the mart is rebuilt, at which point history
  reattaches to the wrong person.
- **All dimensions type 1.** Defensible over 104 days in which nothing has
  changed. **Not reversible**: type 1 keeps no record of what it overwrote, so
  history cannot be added by switching `scd` — the dimension has to be rebuilt
  from bronze, which is the only thing that still holds the past.
- **Fact grain is a badge read**, measured not assumed: unique on
  (cardholder, building, date, minute); only 1,911 distinct without the minute.
  Headcount is therefore `DISTINCTCOUNT`, declared as a measure and never
  precomputed as a column — it is not additive across any dimension.
- **No money column exists anywhere.** F5 says reconcile on money and D2 says
  name the money column at every layer; neither applies. Reconciliation is on
  arrival counts, and D2 tracks `used_presence_this_day` as the one additive
  value that can move without the row count moving.

## Five cleansing rules added to the framework

The eight defects above are **structural** — a grain that is not what everyone
assumes, an array in a text column, a calendar too short, a flag computed once,
a bridge missing a side. No existing rule did what they need without lying:
pointing `deduplicate` at DUP-001 would have destroyed 83 real badge reads to
make a count look tidy.

Added to `ttfabric/cleansing.py` and to the `dryrun.py` simulator:
`assert_grain`, `explode_json_array`, `extend_calendar`,
`recompute_activity_flags`, `flag_incomplete_reference`.

Purely additive — `01_demo-project` and `03_live_demo` still validate at 0
errors.

## Open items

1. **The FTL64 capacity is a trial, shared by three projects, with no declared
   successor.** When it lapses it takes every environment's compute at once,
   prod included. `fallback` is deliberately left absent rather than copying
   the siblings' F2 entry — 2 capacity units cannot run Spark usefully, and
   recording it would silence the warning while guaranteeing the failure.
   This is not theoretical: capacity contention caused three separate failures
   during the first deployment, including the retry that duplicated bronze.
2. **prod is not provisioned.** Only `PushkarDemo_dev` exists
   (`4e558a63-8eb8-43c4-8d17-afb917ce404f`).
3. **RLS matches nobody yet.** The six personas are application logins
   (`enterprise_admin`, `property_manager`), not email addresses, so
   `USERPRINCIPALNAME()` resolves to no row until a real-user-to-persona
   mapping exists. The filter path is correct; the identity join is missing.
   The failure mode is the safe one — no rows rather than the wrong rows.
4. **Data quality is not surfaced on the CxO report.** P2 asks whether it
   should be; it currently lives in D1 only. A pipeline that silently corrects
   data looks exactly like one that silently corrupts it, and this project
   corrects two columns (`date_range`, the 365-day flag). Worth a tile.
5. **The manual prod gate needs required reviewers configured** on the GitHub
   Environment. Nothing generated here enforces it.
6. **Bronze is schema-asymmetric.** The four tables reloaded after the
   duplicate incident carry an `ingest_date` partition column; the other
   eleven, written by the older generator, do not. Two consequences: the
   `schema_match` DQ checks compare against an F2 registry that declares no
   such column, and the idempotency fix only protects the four — a retry on
   any of the other eleven still duplicates. Reloading all fifteen makes
   bronze uniform, at the cost of eleven more Spark sessions on a contended
   capacity.
7. **`Work Day Arrivals` equals `Total Arrivals`** (2,000 of 2,000). Plausible
   for synthetic office-access data generated only on weekdays, but worth
   confirming against the source rather than assuming the filter works.
8. **The DQ monitor and audit notebooks have never been run.** `nb_dq_monitor`
   and `nb_build_audit` are deployed but were not executed, so the 18 D1
   checks and the D2 layer-flow audit are declared and unmeasured.

## Provisioned

`PushkarDemo_dev` — `4e558a63-8eb8-43c4-8d17-afb917ce404f`

| Item | Id |
|---|---|
| `lh_bronze` | `42024aa3-81fe-4e04-aaee-6035aff8a2d5` |
| `lh_silver` | `2a5a422a-17b6-4080-8983-e884b41de824` |
| `wh_gold` | `e120fe2c-38d8-4037-abef-33a0c04d9372` |
| `env_spark` | `49def443-fd33-4ff3-bb42-14152a12080f` |

`wh_gold` returned an **empty id** on creation and resolved only on the
idempotent re-run — warehouse provisioning is async and the id is not in the
create response. A script recording the create response verbatim would have
written an empty id, and every gold write would then have failed against
nothing.

## Reports

Two reports over ONE semantic model (`sm_workplace_access`, 25 measures) --
a second model would be a second definition of Distinct People, and the two
would drift.

| Report | Pages | Source |
|---|---|---|
| `rpt_workplace_access_executive` | Occupancy, Arrival Pattern, Company Presence, Cardholder Activity | Designed from the four KPI areas |
| `rpt_kastle_insights` | Arrival Trends, Activity Trends, Activity By Company, Mobile Credential Usage | Reproduces the real Kastle Insights report from `powerbi/report_images/` |

Measure definitions for the Kastle report were recovered from the screenshots
and checked against the numbers printed on them:

- **Active+** 28% = 7,144 / 25,316 — daily average unique people over unique
  people in the range. The screenshot wording says "the entire date range",
  which the arithmetic shows means the SELECTED range.
- **Max Active+** 45% = 11,300 / 25,316
- **66 Total Days** for a 91-day window — days *with arrivals*, not calendar days

Two definitions could NOT be recovered and are flagged in the spec:

- **Avg Days Per Week** — the printed 1.41 cannot be reproduced from the other
  figures on the page. The measure is a reasonable reading, not Kastle's formula.
- **Mobile credential** — `used_presence_this_day` is assumed to mean Kastle
  Presence. There is no numeric corroboration: the report's 8% is person-level,
  and this source's comparable figure is 32% on synthetic data.

`Used Mobile Credential` is written the long way (`COUNTROWS(FILTER(VALUES(...)))`)
because the obvious `CALCULATE(..., Arrivals[Line Presence Used] = 1)` fails at
query time on this DirectLake model — the column aggregates but cannot be
scanned as a filter, and even a bare `VALUES()` on it errors. Cause not
established.

## Deployed to dev

Fully deployed and smoke-tested on 2026-08-10. All 17 measures return values:
Distinct People 300, Total Arrivals 2,000, Personnel 1,696 / Visitor 304,
30 companies, Active Cardholders 30d 258.

Getting there took five real failures, recorded because none of them announced
its cause:

| # | Symptom | Actual cause |
|---|---|---|
| 1 | `TooManyRequestsForCapacity` HTTP 430 in silver | Capacity contention on the shared FTL64 trial. Not a defect -- a re-run alone got further |
| 2 | `row loss: 6 in, 21 out, -15 unaccounted` | The generated silver notebook asserted the strict row identity, which a row-multiplying rule cannot satisfy. It named a row GAIN as a loss |
| 3 | `point-in-time lookup fanned out: 2,000 became 4,000` on a **type-1** dimension | Bronze had been loaded twice. `append` + `load_pattern: full` means a pipeline RETRY lands a second full snapshot and reports success |
| 4 | `diagnose_notebook.py` raised `IndexError`, then wrote nothing | Two bugs in the diagnostic tool itself, plus its `--scratch-lakehouse` default makes bronze diagnostics silently 404 |
| 5 | Model deployed, every query `Invalid column name 'arrival_event'` | The fact carries the MEASURE TARGET (`arrival_count`), not the business-rule name. P1 bound to the wrong side of the rename |

A sixth was caught by the smoke test rather than by a failure: `Total Cardholders`
read 301 against 300 real cardholders, because `DISTINCTCOUNT` over a dimension
includes the unknown member. The Active measures were unaffected -- their flags
default to 0 on that row -- which is what made it easy to miss.

### Re-deploying

Model before report — `push_reports.py` refuses otherwise,
because a report bound to a missing model publishes and renders nothing, which
is indistinguishable from a data problem.

```bash
python AzureFabricMCP/framework/deploy/push_library.py        --project 04_PushkarDemo --env dev --wait
python AzureFabricMCP/framework/deploy/push_files.py          --project 04_PushkarDemo --env dev
python AzureFabricMCP/framework/deploy/push_items.py          --project 04_PushkarDemo --env dev --create-missing
python AzureFabricMCP/framework/deploy/run_migrations.py      --project 04_PushkarDemo --env dev
python AzureFabricMCP/framework/deploy/push_semantic_model.py --project 04_PushkarDemo --env dev
python AzureFabricMCP/framework/deploy/push_reports.py        --project 04_PushkarDemo --env dev
python AzureFabricMCP/framework/deploy/organise_items.py      --project 04_PushkarDemo --env dev
```

`push_library.py` must run first and must run **after** the wheel rebuild: the
five new rules live in `ttfabric`, and a stale wheel fails at run time with an
unknown-rule `KeyError` rather than at validation.

Then prove it works, because deploying is not the same as working — a model
with a relationship on the wrong column publishes cleanly and returns blank:

```bash
python AzureFabricMCP/framework/tools/query_model.py --project 04_PushkarDemo --smoke
```
