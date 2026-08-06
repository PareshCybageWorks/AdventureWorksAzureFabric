"""
Scaffold a new project from the framework templates.

Creates the folder structure, copies every stage template into it, and prints
the order to fill them in. Without this, starting a project means copying ten
files by hand and knowing which order they depend on -- which is knowledge that
lived only in someone's head.

What it does NOT do
-------------------
It does not fill anything in, and it does not touch Fabric. A scaffolded
project fails validation immediately, on purpose: the templates carry
`<placeholder>` values, and a validator that passed on an unfilled template
would be worthless on a filled one.

Nothing here is destructive. It refuses to write into a directory that already
has specs rather than merging with them.

Usage:
    python new_project.py --name 02_sales-analytics
    python new_project.py --name 02_sales-analytics --tracks fabric,powerbi
    python new_project.py --name 02_sales-analytics --dry-run
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

FRAMEWORK = Path(__file__).resolve().parent.parent
TEMPLATES = FRAMEWORK / "templates"

# Order matters: this is the dependency chain, not an alphabetical listing.
# A stage cannot be written before the ones it reads from exist.
ORDER = [
    ("fabric",  "01-scaffolding", "Workspaces, capacity, topology, storage, naming."),
    ("fabric",  "02-sources",     "Where data comes from, and what is known to be wrong with it."),
    ("fabric",  "03-bronze",      "Landing. Needs F2 for entity names."),
    ("fabric",  "04-silver",      "Cleansing rules. Needs F3 for source tables."),
    ("fabric",  "05-gold",        "Star schema and reporting views. Needs F4."),
    ("powerbi", "01-semantic-model", "Model over gold. Needs F5 for column types."),
    ("powerbi", "02-reports",     "Reports over the model. Needs P1 for field names."),
    ("dataops", "01-monitoring",  "Expectations and enforcement. Needs F3-F5."),
    ("dataops", "02-audit",       "Row and value flow bronze to gold. Needs F3-F5."),
    ("cicd",    "01-pipeline",    "Workflows and gates. Needs F1 for environments."),
]

SUBDIRS = ["fabric", "powerbi", "dataops", "cicd", "data", "docs", ".config",
           "generated/notebooks", "generated/pipelines",
           "generated/migrations", "generated/model", "generated/reports"]

GITIGNORE = """# generated/ is deliberately NOT ignored.
#
# The no-drift CI gate re-runs every generator with --check and compares against
# what is committed. With nothing to compare against, the gate cannot fail --
# which is worse than not having it. The deploy scripts also read from here.

__pycache__/
*.pyc

# .config/ holds this project's secrets.
#
# Ignore the whole directory rather than listing filenames. A denylist misses
# the file nobody anticipated -- .env.local, .env.prod, creds.json, a pasted
# token in notes.txt -- and it only has to miss once. The two files that ARE
# safe to commit are re-included explicitly below.
.config/*
!.config/.env.example
!.config/README.md

# Belt and braces: a .env anywhere in the project, not only under .config/.
.env
.env.*
!.env.example
"""

# Committed template. The real `.env` sits beside it and is gitignored, so the
# pair documents every variable a project needs without any project ever
# holding a secret in version control.
ENV_EXAMPLE = """\
# =============================================================================
# {name} -- secret template
# =============================================================================
# COPY THIS FILE to `.env` in this directory and fill it in. Never edit this
# file with real values: `.env` is gitignored, `.env.example` is committed, and
# the whole point of the pair is that one of them can be read by anyone.
#
#   cp .config/.env.example .config/.env
#
# Loaded by framework/deploy/_secrets.py::load_project_env, which walks up from
# the working directory looking for `.config/.env`, or reads $FABRIC_PROJECT if
# that is set.
#
# AN EXISTING ENVIRONMENT VARIABLE ALWAYS WINS over a value in this file. CI
# injects secrets as environment variables from repository secrets, and a file
# checked out beside the specs must never silently override what the runner
# set.
#
# Nothing here belongs in a spec. Specs carry REFERENCES only -- ${{VAR}} or
# keyvault://vault/name -- and validate.py::check_secrets fails the build on a
# literal.
# =============================================================================

# ---- Fabric -----------------------------------------------------------------
# Service principal used by the deploy scripts. Locally these can be left empty
# and `az login` used instead -- _secrets.py tries the environment first and the
# deploy scripts fall back to the Azure CLI credential.
FABRIC_TENANT_ID=
FABRIC_CLIENT_ID=
AZURE_CLIENT_SECRET=

# ---- Landing zone -----------------------------------------------------------
# Referenced by fabric/02-sources.yaml connection.location.
LANDING_ACCOUNT=
LANDING_CONTAINER=

# ---- Per-source credentials -------------------------------------------------
# Add one block per source registered in fabric/02-sources.yaml. The variable
# names must match the ${{...}} references that spec declares.
"""

CONFIG_README = """\
# `.config/` — project secrets

Everything in this directory is **per project**. Two projects on one machine do
not share credentials, and no secret sits beside a spec where it could be
committed by accident.

| File | Committed | Purpose |
|---|---|---|
| `.env.example` | yes | The template. Documents every variable and what it is for. |
| `.env` | **no** | Real values. Gitignored. |
| anything else here | **no** | The `.gitignore` ignores the whole directory except the two files above. |

## Setup

```bash
cp .config/.env.example .config/.env
```

## How it is read

`framework/deploy/_secrets.py::load_project_env` walks up from the working
directory looking for `.config/.env`, or reads `$FABRIC_PROJECT` if set. It is
called lazily on the first `resolve()`, so scripts do not have to load it
themselves.

**An existing environment variable always wins.** CI injects secrets as
environment variables, and a file checked out beside the specs must not
silently override what the runner set.

## What does not go here

Specs never hold a secret — they hold a reference, `${VAR}` or
`keyvault://vault/name`. `validate.py::check_secrets` fails the build on a
literal.
"""


def readme(name: str, stages: list[tuple[str, str, str]]) -> str:
    lines = [
        f"# {name}",
        "",
        "Scaffolded from the TechTonic Fabric framework. **Every spec below is a",
        "template and must be filled in** — validation fails until they are, which",
        "is the intended starting state.",
        "",
        "## Fill these in, in this order",
        "",
        "The order is a dependency chain, not a preference: each stage reads names",
        "and types from the ones above it.",
        "",
        "| # | Spec | Depends on |",
        "|---|---|---|",
    ]
    for index, (track, stage, why) in enumerate(stages, 1):
        lines.append(f"| {index} | `{track}/{stage}.yaml` | {why} |")

    lines += [
        "",
        "Each has a procedure in `framework/prompts/<track>/<stage>.md` — read it",
        "before filling the spec in. They carry the failure modes, not just the",
        "field list.",
        "",
        "## After each stage",
        "",
        "```bash",
        f"python <framework>/generators/validate.py --project {name}",
        "```",
        "",
        "Validation is authoritative. Every check in it exists because the mistake",
        "it catches actually happened on a real project.",
        "",
        "## Once the specs are filled in",
        "",
        "```bash",
        "# generate",
        f"python <framework>/generators/generate_notebooks.py --specs {name} --out {name}/generated/notebooks",
        f"python <framework>/generators/generate_pipelines.py --specs {name} --out {name}/generated/pipelines",
        f"python <framework>/generators/generate_ddl.py       --specs {name} --out {name}/generated/migrations",
        f"python <framework>/generators/generate_tmdl.py      --specs {name} --out {name}/generated/model",
        f"python <framework>/generators/generate_report.py    --specs {name} --out {name}/generated/reports",
        "",
        "# provision and deploy",
        f"python <framework>/deploy/create_workspaces.py  --project {name} --capacity <id>",
        f"python <framework>/deploy/provision_storage.py  --project {name} --env dev",
        f"python <framework>/deploy/push_library.py       --project {name} --env dev --wait",
        f"python <framework>/deploy/push_items.py         --project {name} --env dev --create-missing",
        f"python <framework>/deploy/run_migrations.py     --project {name} --env dev",
        f"python <framework>/deploy/push_semantic_model.py --project {name} --env dev",
        f"python <framework>/deploy/push_reports.py       --project {name} --env dev",
        f"python <framework>/deploy/organise_items.py     --project {name} --env dev",
        "```",
        "",
        "`organise_items` runs **last**: it files only what exists when it runs, so",
        "before the model and reports are deployed it would leave them at the",
        "workspace root and report success.",
        "",
        "## Then prove it works",
        "",
        "```bash",
        f"python <framework>/tools/query_model.py     --project {name} --smoke",
        f"python <framework>/tools/verify_bindings.py --project {name} --env dev",
        "```",
        "",
        "Deploying is not the same as working. A model with a relationship on the",
        "wrong column publishes cleanly and returns blank.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True,
                        help="project directory, e.g. 02_sales-analytics")
    parser.add_argument("--into", default=".",
                        help="where to create it (default: current directory)")
    parser.add_argument("--tracks", default="fabric,powerbi,dataops,cicd",
                        help="comma-separated subset of tracks to scaffold")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    wanted = {t.strip() for t in args.tracks.split(",") if t.strip()}
    stages = [s for s in ORDER if s[0] in wanted]
    if not stages:
        print(f"  ERROR  no stages match tracks {sorted(wanted)}")
        return 2

    root = Path(args.into).resolve() / args.name

    # Refuses rather than merges. Half-scaffolding over an existing project
    # would overwrite filled-in specs with templates.
    if root.exists() and any((root / t).glob("*.yaml") for t in wanted if (root / t).is_dir()):
        print(f"  REFUSED  {root} already contains specs. Scaffolding over it "
              f"would replace filled-in specs with templates.")
        return 2

    print(f"project  {args.name}")
    print(f"at       {root}")
    print(f"tracks   {', '.join(sorted(wanted))}")
    print()

    planned: list[tuple[Path, Path | None]] = []
    for track, stage, _ in stages:
        template = TEMPLATES / track / f"{stage}.yaml"
        if not template.exists():
            print(f"  MISSING  no template for {track}/{stage} -- the framework "
                  f"has a gap, not this project")
            continue
        planned.append((root / track / f"{stage}.yaml", template))

    if args.dry_run:
        for directory in SUBDIRS:
            if directory.split("/")[0] in wanted or not directory[0].isalpha() \
                    or directory.startswith(("data", "docs", "generated")):
                print(f"  mkdir   {directory}")
        for target, _ in planned:
            print(f"  create  {target.relative_to(root)}")
        print(f"  create  README.md")
        print(f"  create  .gitignore")
        print("\ndry run -- nothing written")
        return 0

    for directory in SUBDIRS:
        head = directory.split("/")[0]
        if head in ("fabric", "powerbi", "dataops", "cicd") and head not in wanted:
            continue
        (root / directory).mkdir(parents=True, exist_ok=True)

    for target, template in planned:
        shutil.copyfile(template, target)
        print(f"  created {target.relative_to(root)}")

    (root / "README.md").write_text(readme(args.name, stages), encoding="utf-8")
    (root / ".gitignore").write_text(GITIGNORE, encoding="utf-8")
    print(f"  created README.md")
    print(f"  created .gitignore")

    # Secrets scaffolding. Only the TEMPLATE is written -- never a `.env`, so
    # this can never create a file that looks configured but holds nothing, and
    # a real one is never overwritten.
    (root / ".config" / ".env.example").write_text(
        ENV_EXAMPLE.format(name=args.name), encoding="utf-8")
    (root / ".config" / "README.md").write_text(CONFIG_README, encoding="utf-8")
    print(f"  created .config/.env.example")
    print(f"  created .config/README.md")

    print()
    print("Fill the specs in this order -- each reads names from the one above:")
    for index, (track, stage, why) in enumerate(stages, 1):
        print(f"  {index}. {track}/{stage}.yaml")
        print(f"     {why}")
        print(f"     procedure: framework/prompts/{track}/{stage}.md")

    print()
    print("Validation fails until they are filled in. That is the intended state:")
    print(f"  python framework/generators/validate.py --project {args.name}")
    print()
    print("To fill them in as a conversation rather than by hand, follow")
    print("framework/prompts/00-interview.md. To find out where you are at any")
    print("point -- including in a session that has lost all context:")
    print(f"  python framework/tools/project_status.py --project {args.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
