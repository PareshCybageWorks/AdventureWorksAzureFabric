"""
Report how far a project has been filled in, and what to do next.

Why this exists
---------------
Building a project is a ten-stage interview that will not finish in one
sitting. Something has to answer "where were we" when it resumes.

That something is deliberately NOT a progress file. A separate record of what
is done is a second source of truth, and it drifts the first time someone edits
a spec without updating it -- at which point it confidently points at the wrong
stage. Status is derived from the specs themselves instead: a stage is
unfinished if its file is missing, still carries template placeholders, or
fails validation. Those cannot disagree with reality because they ARE reality.

Usage:
    python project_status.py --project 02_sales-analytics
    python project_status.py --project 02_sales-analytics --json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

FRAMEWORK = Path(__file__).resolve().parent.parent

# The dependency chain, not an alphabetical listing. A stage cannot be
# interviewed before the ones it reads names and types from.
ORDER = [
    ("fabric",  "01-scaffolding",    "Workspaces, capacity, topology, storage, naming"),
    ("fabric",  "02-sources",        "Where data comes from, and what is wrong with it"),
    ("fabric",  "03-bronze",         "Landing. Needs F2 for entity names"),
    ("fabric",  "04-silver",         "Cleansing rules. Needs F3 for source tables"),
    ("fabric",  "05-gold",           "Star schema and views. Needs F4"),
    ("powerbi", "01-semantic-model", "Model over gold. Needs F5 for column types"),
    ("powerbi", "02-reports",        "Reports over the model. Needs P1 for field names"),
    ("dataops", "01-monitoring",     "Expectations and enforcement. Needs F3-F5"),
    ("dataops", "02-audit",          "Row and value flow bronze to gold. Needs F3-F5"),
    ("cicd",    "01-pipeline",       "Workflows and gates. Needs F1 for environments"),
]

STAGE_LABEL = {
    ("fabric", "01-scaffolding"): "F1", ("fabric", "02-sources"): "F2",
    ("fabric", "03-bronze"): "F3", ("fabric", "04-silver"): "F4",
    ("fabric", "05-gold"): "F5", ("powerbi", "01-semantic-model"): "P1",
    ("powerbi", "02-reports"): "P2", ("dataops", "01-monitoring"): "D1",
    ("dataops", "02-audit"): "D2", ("cicd", "01-pipeline"): "C1",
}

PLACEHOLDER = re.compile(r"<[a-z_][a-z0-9_.-]*>")


def strip_comments(text: str) -> str:
    """Drop comment lines and trailing comments.

    Templates document their own placeholders in comments, and an optional
    block is offered commented out. Counting those would report a finished
    stage as unfilled forever. This is a deliberate approximation: a `#` inside
    a quoted string is treated as a comment, which these specs do not contain
    but a future one might.
    """
    out = []
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        out.append(line.split(" #")[0])
    return "\n".join(out)


def placeholders(path: Path) -> list[str]:
    text = strip_comments(path.read_text(encoding="utf-8"))
    seen: list[str] = []
    for token in PLACEHOLDER.findall(text):
        if token not in seen:
            seen.append(token)
    return seen


def validation_errors(project: Path) -> tuple[int, list[str]]:
    """Errors from the authoritative validator, not a reimplementation here."""
    result = subprocess.run(
        [sys.executable, str(FRAMEWORK / "generators" / "validate.py"),
         "--project", str(project)],
        capture_output=True, text=True)
    lines = [l.strip() for l in result.stdout.splitlines() if "ERROR" in l]
    return len(lines), lines


def assess(project: Path) -> list[dict]:
    stages = []
    for track, stage, why in ORDER:
        path = project / track / f"{stage}.yaml"
        label = STAGE_LABEL[(track, stage)]

        if not path.exists():
            state, remaining = "missing", []
        else:
            remaining = placeholders(path)
            state = "template" if remaining else "filled"

        stages.append({"stage": label, "track": track, "name": stage,
                       "file": f"{track}/{stage}.yaml", "depends": why,
                       "state": state, "placeholders": remaining})
    return stages


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    project = Path(args.project).resolve()
    if not project.exists():
        print(f"  no project at {project}")
        return 2

    stages = assess(project)
    done = [s for s in stages if s["state"] == "filled"]
    next_stage = next((s for s in stages if s["state"] != "filled"), None)

    # Validation is only meaningful once every stage has real values in it --
    # a template fails on its own placeholders and buries anything real.
    errors, error_lines = (0, [])
    if not next_stage:
        errors, error_lines = validation_errors(project)

    if args.json:
        print(json.dumps({"project": project.name, "stages": stages,
                          "complete": len(done), "total": len(stages),
                          "next": next_stage["stage"] if next_stage else None,
                          "validation_errors": error_lines}, indent=2))
        return 0

    print(f"  project  {project.name}")
    print(f"  progress {len(done)}/{len(stages)} stages filled")
    print()

    mark = {"filled": "[x]", "template": "[ ]", "missing": " ? "}
    for stage in stages:
        line = f"  {mark[stage['state']]} {stage['stage']:<3} {stage['file']:<31}"
        if stage["state"] == "missing":
            line += "not created"
        elif stage["state"] == "template":
            shown = ", ".join(stage["placeholders"][:4])
            more = len(stage["placeholders"]) - 4
            line += f"{len(stage['placeholders'])} to fill: {shown}"
            if more > 0:
                line += f", +{more}"
        print(line)

    print()
    if next_stage:
        print(f"  NEXT   {next_stage['stage']} -- {next_stage['file']}")
        print(f"         {next_stage['depends']}")
        print(f"         procedure: framework/prompts/{next_stage['track']}/"
              f"{next_stage['name']}.md")
    elif errors:
        print(f"  All stages filled, but validation reports {errors} error(s):")
        for line in error_lines[:8]:
            print(f"    {line}")
        print()
        print(f"  Fix these before generating. Validation is authoritative.")
    else:
        print("  All stages filled and validation passes. Ready to generate:")
        print(f"    python framework/generators/validate.py --project {project.name}")
        print(f"    python framework/tools/dryrun.py       --project {project.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
