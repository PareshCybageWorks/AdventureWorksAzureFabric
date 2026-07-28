# C1 — CI/CD Pipeline

Produce `<project>/cicd/01-pipeline.yaml`: the workflows, gates and promotion
rules, from which `.github/workflows/*.yml` are generated.

Contract: `framework/contracts/cicd/01-pipeline.schema.json` — authoritative.
Template: `framework/templates/cicd/01-pipeline.yaml`.

**Depends on F1** for the environment names, and on every other stage for the
commands it runs.

---

## Why generate workflows at all

Hand-written workflows drift from the gates the spec declares, and the spec is
what people read. Generating them makes the spec the only place either exists,
and `ci-validate` fails on drift — so an edit to the YAML that is not in the
spec cannot survive a pull request.

---

## Ask the user

1. **What is the repository root?** Step paths are relative to it, not to the
   framework folder. This is the single most common way a generated workflow
   fails on its first run.
2. **Which branch maps to which environment?**
3. **Who approves production, and against what criteria?**
4. **Which secrets exist, and under what names?** Names only — a literal value
   fails `check_secrets`.

---

## Derive

- **Escalate strictness, do not apply it uniformly.** Errors block everywhere.
  Warnings should block at qa and above, not on every pull request: blocking a
  PR on "17 checks record no calibration note" teaches people to silence
  warnings rather than act on them.
- **Every deploying job declares an `environment`.** That is what carries
  required reviewers and scoped secrets. A job deploying to prod without one has
  no approval gate however many gates the spec lists.
- **`cancel-in-progress: false` on deploys.** A half-applied migration is worse
  than a queued run.
- **Deploy the model before the reports**, and run migrations before either.

---

## Gate — refuse to proceed if

- **A step names a script that does not exist.** Resolved against the repo at
  validation, because otherwise it fails on someone else's pull request. This
  check earned its place immediately: the first version of this spec referenced
  `sync_workspace.py`, which never existed, and `framework/lib/tests`, which had
  moved.
- **A job deploys but declares no environment.**
- **A manual gate names no approver.** That is not a gate.
- **An automated gate has no job with a matching id.** Nothing enforces it, and
  the spec reads as though something does.
- **A gate or job references an environment not in F1.**
- **Two workflows write to the same file** — one silently overwrites the other.

---

## What generation cannot enforce

**Manual gates live outside the repository.** Required reviewers are configured
on the GitHub Environment; no generated file can create them. The generator
prints them at the end of every run, and validation warns, because the failure
mode is a spec that documents an approval nobody ever configured.

---

## Then

```bash
python framework/generators/validate.py           --project <p> --track cicd
python framework/generators/generate_workflows.py --specs   <p> --out <repo-root>
```

Then **run the CI job locally before pushing it**:

```bash
python framework/generators/validate.py --project <p>
python framework/tests/test_monitoring.py
```

A workflow whose first real execution is on a pull request is a workflow whose
first failure is public.
