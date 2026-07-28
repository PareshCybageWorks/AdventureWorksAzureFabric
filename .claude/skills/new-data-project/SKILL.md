---
name: new-data-project
description: Interactively design a new Microsoft Fabric + Power BI data project by interviewing the user, one stage at a time, producing validated specs. Use when someone wants to start a new data project, add a data domain, or resume an unfinished one.
---

# New data project

Build a Fabric + Power BI project by interviewing the user across ten stages,
writing a validated spec at each one.

## Before anything else

Find out where you are. This works on a project you have never seen, in a
session with no memory of the last one:

```bash
python framework/tools/project_status.py --project <name>
```

- **No project yet** → create it, then start at F1.
- **Partly filled** → resume at the stage it names. Do not restart, and do not
  re-ask questions whose answers are already in the specs — read them first.
- **All filled** → validate and move to generation, not more interviewing.

Status is derived from the specs themselves, so it cannot disagree with them.
Trust it over the conversation, including over your own summary of it.

## Creating one

```bash
python framework/tools/new_project.py --name <NN_project-name>
```

Copies every template in and creates the folder structure. It deliberately does
not fill anything in — a scaffolded project fails validation immediately,
which is the correct starting state.

## Then run the interview

**Read `framework/prompts/00-interview.md` and follow it.** It carries how to
ask, what to derive rather than ask, what to refuse to guess, and the failure
modes that make interviews produce plausible-but-wrong specs.

Per stage, its own prompt at `framework/prompts/<track>/<stage>.md` holds the
questions and the exit gate. Read them one stage at a time — all ten at once is
a great deal of text, most of it not yet relevant.

## The rules that matter most

- **Batch three to five questions**, never one at a time and never twenty.
- **Always recommend an option.** The user is choosing between things they have
  not compared; you have.
- **Never ask what the spec already implies.** Derive it.
- **Validate after every stage** — `framework/generators/validate.py`. Do not
  advance past errors.
- **Honour each stage's exit gate**, including when the user would rather move
  on. Every gate exists because skipping it cost someone a day.
- **Refuse to guess four things**: a natural key, the grain of a fact, a money
  column's name per layer, and whether a dimension needs history. Each is
  silent when wrong.

## Finishing

```bash
python framework/generators/validate.py --project <name>
python framework/tools/dryrun.py        --project <name>
```

`dryrun` exercises the specs against sample data with no Fabric involved, so
the first deployment is not also the first execution.

Provisioning is `framework/prompts/fabric/01-scaffolding.md` Step 5; the
repository side is `docs/CI-SETUP.md`.
