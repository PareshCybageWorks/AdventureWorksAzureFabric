"""
Prove the validator FAILS on the defects it exists to catch.

Why this exists
---------------
A check that has only ever passed proves nothing. Every case below is a defect
that actually reached a deployed Fabric artefact, passed validation, and failed
somewhere expensive -- at model import, at query time, or inside Spark where
Fabric reports only "session failed".

Each case re-introduces the defect into a COPY of a real project and asserts
the validator reports it. Nothing writes to the project.

This caught two flaws in the checks themselves before they were trusted:

  - view-columns first flagged `is_current` on an SCD2 dimension, which is
    injected by merge_scd2 rather than declared. A false error is worse than
    the gap it closes, because the response is to stop believing the check.
  - view-columns then MISSED the rename case, because it treated a business
    rule's target as a fact column when the measure renames it away -- which
    is the exact defect it was written for.

Mutations operate on the parsed spec, not on file text, so they work against
any project rather than one project's wording.

Usage:
    python test_validator_catches.py
    python test_validator_catches.py --project ../03_live_demo
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

FRAMEWORK = Path(__file__).resolve().parent.parent
VALIDATE = FRAMEWORK / "generators" / "validate.py"


# --- mutations -------------------------------------------------------------
# Each returns a reason string when it could apply, or None when the project
# has nothing to mutate. A project without views cannot exercise view-columns,
# and that is a skip, not a failure.

def break_business_key(specs: dict) -> str | None:
    """business_key naming a SOURCE column the projection renames away."""
    gold = specs.get("fabric/05-gold")
    for dimension in (gold or {}).get("dimensions", []):
        for column in dimension.get("columns") or []:
            if column.get("source") and column["source"] != column.get("target"):
                dimension["business_key"] = [column["source"]]
                return f"{dimension['name']}.business_key -> {column['source']!r}"
    return None


def break_case_collision(specs: dict) -> str | None:
    """Two model columns differing only by case."""
    model = specs.get("powerbi/01-semantic-model")
    for table in (model or {}).get("tables", []):
        columns = table.get("columns") or []
        if len(columns) < 2:
            continue
        first = columns[0].get("name", columns[0]["source"])
        columns[1]["name"] = first.lower()
        if columns[1]["name"] == first:
            columns[1]["name"] = first.upper()
        return f"{table['name']}: duplicate of {first!r} differing by case"
    return None


def break_dax_reference(specs: dict) -> str | None:
    """A measure referencing a SOURCE column name instead of the display name."""
    model = specs.get("powerbi/01-semantic-model")
    for table in (model or {}).get("tables", []):
        for column in table.get("columns") or []:
            display = column.get("name")
            if not display or display == column["source"]:
                continue
            for measure in (model or {}).get("measures", []):
                if f"'{table['name']}'[{display}]" in measure.get("expression", ""):
                    measure["expression"] = measure["expression"].replace(
                        f"'{table['name']}'[{display}]",
                        f"'{table['name']}'[{column['source']}]")
                    return (f"measure {measure['name']!r} -> "
                            f"'{table['name']}'[{column['source']}]")
    return None


def break_view_column(specs: dict) -> str | None:
    """A bi view reading a column no gold table carries."""
    gold = specs.get("fabric/05-gold")
    for view in (gold or {}).get("views", []) or []:
        sql = view.get("sql", "")
        for fact in (gold or {}).get("facts", []):
            for measure in fact.get("measures", []) or []:
                target = measure["target"]
                if f".{target}" in sql:
                    view["sql"] = sql.replace(f".{target}",
                                              f".{target}_does_not_exist", 1)
                    return f"{view['name']} -> {target}_does_not_exist"
    return None


CASES = [
    ("business_key naming a renamed source column",
     break_business_key, "dimension-keys",
     "five gold dimensions failed inside Spark"),
    ("model columns colliding case-insensitively",
     break_case_collision, "model-names",
     "the semantic model refused to import"),
    ("DAX referencing a source column name",
     break_dax_reference, "model-names",
     "every measure failed at query time"),
    ("bi view reading a non-existent column",
     break_view_column, "view-columns",
     "three views failed at deploy"),
]

SPEC_FILES = {
    "fabric/05-gold": "fabric/05-gold.yaml",
    "powerbi/01-semantic-model": "powerbi/01-semantic-model.yaml",
}


def run_case(project: Path, label: str, mutate, expected: str,
             consequence: str) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / project.name
        shutil.copytree(project, copy, ignore=shutil.ignore_patterns(
            "generated", "data", ".config", ".git"))

        specs = {}
        for key, relative in SPEC_FILES.items():
            path = copy / relative
            if path.exists():
                specs[key] = yaml.safe_load(path.read_text(encoding="utf-8"))

        reason = mutate(specs)
        if reason is None:
            return "SKIP"

        for key, relative in SPEC_FILES.items():
            if key in specs:
                (copy / relative).write_text(
                    yaml.safe_dump(specs[key], sort_keys=False), encoding="utf-8")

        result = subprocess.run(
            [sys.executable, str(VALIDATE), "--project", str(copy)],
            capture_output=True, text=True, cwd=str(FRAMEWORK.parent))
        caught = any(expected in line and "ERROR" in line
                     for line in result.stdout.splitlines())
        print(f"  {'CAUGHT' if caught else 'MISSED'}  {label}")
        print(f"          introduced: {reason}")
        print(f"          would have: {consequence}")
        return "CAUGHT" if caught else "MISSED"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default=None,
                        help="project to mutate (default: first sibling with "
                             "both a gold and a semantic-model spec)")
    args = parser.parse_args()

    if args.project:
        project = Path(args.project).resolve()
    else:
        candidates = [
            p for p in sorted(FRAMEWORK.parent.iterdir())
            if p.is_dir() and (p / "fabric" / "05-gold.yaml").exists()
            and (p / "powerbi" / "01-semantic-model.yaml").exists()
        ]
        if not candidates:
            print("no project with both a gold and semantic-model spec found")
            return 0
        project = candidates[-1]

    print(f"mutating a copy of {project.name}\n")
    results = [run_case(project, *case) for case in CASES]

    missed = results.count("MISSED")
    skipped = results.count("SKIP")
    print()
    print(f"{results.count('CAUGHT')} caught, {missed} missed, {skipped} skipped")
    if missed:
        print("\nA MISSED case is a defect the validator would let through to a "
              "deploy.")
    return 1 if missed else 0


if __name__ == "__main__":
    sys.exit(main())
