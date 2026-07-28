# Stage 0 — The Interview

How to build a data project *with* someone rather than for them.

This is the entry point. It sequences the ten stage prompts, each of which
already carries its own questions and gates. Read this once, then work stage by
stage — do not read all ten prompts up front, they are large and most of what
they say will not apply until you reach them.

---

## The shape of it

Ten stages, in dependency order. Each reads names and types from the ones above
it, which is why the order is not negotiable:

```
F1 scaffolding  →  F2 sources  →  F3 bronze  →  F4 silver  →  F5 gold
                                                                  ↓
                        C1 cicd     D1 monitoring   D2 audit   P1 model
                                                                  ↓
                                                              P2 reports
```

A stage is finished when its spec has no placeholders left and `validate.py`
passes. Nothing else marks it done — there is no progress file to fall out of
step with the specs.

**Always start by asking where you are:**

```bash
python framework/tools/project_status.py --project <name>
```

That works on a project you have never seen, in a session that has lost all
context. Trust it over your memory of the conversation.

---

## How to ask

**Batch three to five questions at a time**, not one, and not twenty. One at a
time turns a design conversation into an interrogation; twenty gets abandoned
halfway and answered carelessly in the second half.

**Always offer a recommendation.** "Medallion or mesh?" is a research task for
the user. "I'd suggest medallion — one team, one domain, and mesh's per-domain
ownership costs more than it returns below about four domains. Change it?" is a
decision they can make in five seconds. Most people are choosing between
options they have not compared; you have.

**Never ask what you can derive.** Table names follow from entity names,
bronze names follow from sources, surrogate keys follow from the grain. Asking
for something the spec already implies makes the user do your arithmetic, and
they will get it wrong more often than you would.

**Ask for the reason, not just the value.** Several specs carry `rationale` and
`note` fields, and they exist because a threshold with no recorded origin gets
loosened the first time it fires. When someone says "alert at 5%", ask where 5%
came from. If the answer is "it felt right", record that — an honest guess
labelled as one is far more useful later than a number with a false pedigree.

**Let them say "you decide".** Take it, choose, state what you chose and why,
and move on. Do not re-ask.

---

## What to ask first

Before any stage, get the frame. Five questions, one round:

1. **What decision does this let someone make that they cannot make today?**
   If there is no answer, the project has no consumer and everything after is
   guesswork.
2. **Which source systems, and who owns each?** Ownership matters as much as
   the name — it determines who you ask when the data is wrong.
3. **Who reads the output, and in what?** Power BI, an app, a downstream feed.
   This decides whether P1 and P2 are even in scope.
4. **How fresh must it be?** Daily is a different pipeline from every fifteen
   minutes, and the difference lands in F3 and D1.
5. **What is already known to be wrong with the data?** Ask early. Everyone has
   an answer, it is never in a document, and F2 has a field for it.

Record the answers where they will be read: the project README, then the
relevant spec fields as you reach them.

---

## Then, per stage

For each stage in order:

1. **Read that stage's prompt** — `framework/prompts/<track>/<stage>.md`. Its
   "Ask the user" section is the question list; do not invent your own.
2. **Ask, in batches.** Carry forward what earlier stages already established.
3. **Fill the spec.** Copy from `framework/templates/`, replace placeholders,
   keep the comments — they explain the traps, and a project that strips them
   loses the reason each field exists.
4. **Validate before moving on:**
   ```bash
   python framework/generators/validate.py --project <name>
   ```
5. **Check the stage's own exit gate.** Each prompt has one. It lists the
   conditions under which you must refuse to proceed — honour them, including
   when the user would rather move on.

Do not batch two stages together to save time. F4 written before F3 is settled
produces cleansing rules for tables that turn out to be named differently.

---

## Refuse to guess these

Everything else you can propose a sensible default for. These four you cannot,
because a wrong answer is silent:

- **A natural key.** Guess it and SCD2 either explodes into duplicate versions
  or silently overwrites history. Ask which column identifies one real-world
  thing, and confirm it is unique in the source rather than assuming it.
- **The grain of a fact.** "One row per what?" If the user cannot answer,
  neither can the model, and every measure will be subtly wrong rather than
  obviously broken.
- **A money column's name at each layer.** It changes on the way through
  (`subtotal` → `subtotal` → `line_revenue`). D2 audits value flow and cannot
  find a column you invented.
- **Whether a dimension needs history.** SCD2 versus overwrite is a business
  question about whether last year's report should still say what it said last
  year. It is not a technical preference.

---

## Where interviews go wrong

**Accepting "all of it" as a scope.** Ask which three questions the first
release must answer. A project that lands narrow and works beats one that is
still being specified.

**Believing the source is clean.** It is not. F2 has `known_issues` because
every project has them and nobody volunteers them. Ask what the last data
problem was and who noticed.

**Designing the report first.** People describe the dashboard they imagine, and
it is useful — but as a statement of what the model must support, not as a
layout to build now. Capture it, then work forwards from sources.

**Letting a threshold be invented in the room.** "Alert if nulls exceed 5%" with
no basis becomes noise, then gets ignored, then gets removed. Ask what today's
value is. If nobody knows, that is D1's first job, and the honest spec says the
threshold is provisional.

**Going quiet for ten stages.** Show the specs as they are written. A user who
sees `04-silver.yaml` will spot a wrong rule immediately, and they will not spot
it in a summary of one.

---

## When every stage is filled

```bash
python framework/tools/project_status.py --project <name>     # confirms ready
python framework/generators/validate.py  --project <name>
python framework/tools/dryrun.py         --project <name>     # no Fabric needed
```

`dryrun` runs the spec against real sample data without touching Fabric, so the
first real deployment is not also the first execution.

Only then provision — `framework/prompts/fabric/01-scaffolding.md` Step 5, and
`docs/CI-SETUP.md` for the repository side.

---

## Related

- `framework/tools/new_project.py` — creates the folders and copies templates
- `framework/tools/project_status.py` — where are we, resumable, derived
- `framework/INDEX.md` — the whole framework in one screen
