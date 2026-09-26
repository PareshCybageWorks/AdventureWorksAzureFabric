"""
Generate warehouse DDL from a project's gold mapping.

Emits versioned, idempotent migrations rather than applying changes directly,
so the gold schema is reviewable in a pull request and every environment
converges on the same state.

What is and is not generated
----------------------------
The `dbo` physical tables are NOT generated. Spark creates them through the
warehouse connector when the gold notebooks run, and issuing CREATE TABLE here
would race that -- two definitions of the same table, disagreeing the moment a
column changes.

The `bi` views are NOT generated here either, despite being the surface Power
BI binds to. They select from the `dbo` tables Spark creates, so they cannot
exist until after the first gold run -- emitting them as a deploy-time
migration failed with `Invalid object name 'dbo.fct_sales'` on a fresh
environment. They are built by `nb_build_bi_views` as the last step of the gold
pipeline, from this same spec, so there is one definition and one mechanism.

So this generator currently emits ONE migration: the two schemas. That is not a
placeholder -- it is the only DDL that can safely run before anything else
exists.

Usage:
    python generate_ddl.py --specs ./01_demo-project --out ./01_demo-project/generated/migrations
    python generate_ddl.py --specs ./01_demo-project --out ... --check
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _specs import load as load_spec

HEADER = """-- =============================================================================
-- {filename}
-- =============================================================================
-- GENERATED from the gold mapping by framework/generators/generate_ddl.py.
-- Do not edit by hand: change the spec and regenerate, or the next run
-- overwrites this file and the drift is silent.
--
-- Idempotent. CREATE OR ALTER means re-running converges rather than failing,
-- so a migration can be applied to an environment in any state.
-- =============================================================================

"""


def schema_ddl(schema: str) -> str:
    """Create a schema only when absent.

    Fabric Warehouse has no CREATE SCHEMA IF NOT EXISTS, so the guard is
    explicit and the statement is wrapped -- EXEC is required because CREATE
    SCHEMA must be the first statement in its own batch.
    """
    return (
        f"IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = '{schema}')\n"
        f"    EXEC('CREATE SCHEMA [{schema}]');\n"
    )


def view_ddl(view: dict, default_schema: str) -> str:
    schema = view.get("schema", default_schema)
    body = view["sql"].rstrip().rstrip(";")

    lines = [f"-- {view['name']}"]
    if view.get("description"):
        lines.append(f"--   {' '.join(view['description'].split())}")
    if view.get("grain"):
        lines.append(f"--   Grain: {' '.join(view['grain'].split())}")

    lines.append(f"CREATE OR ALTER VIEW [{schema}].[{view['name']}] AS")
    lines.append(body + ";")
    return "\n".join(lines) + "\n"


def build(gold: dict) -> dict[str, str]:
    """Return {filename: sql}. Numbered so application order is unambiguous."""
    reporting = gold["defaults"]["reporting_schema"]
    physical = gold["defaults"]["physical_schema"]
    files: dict[str, str] = {}

    schemas = HEADER.format(filename="V001__schemas.sql")
    schemas += "-- Both schemas the gold layer uses.\n"
    schemas += "--   physical: star-schema tables, written by the gold notebooks\n"
    schemas += "--   reporting: views Power BI binds to; no pipeline reads them\n\n"
    for schema in dict.fromkeys([physical, reporting]):
        schemas += schema_ddl(schema) + "\n"
    files["V001__schemas.sql"] = schemas

    # The `bi` views are NOT emitted here any more. They select from dbo tables
    # that Spark creates through the warehouse connector, so they cannot exist
    # until after the first gold run -- a deploy-time migration failed with
    # `Invalid object name 'dbo.fct_sales'` on a fresh environment. They are
    # built by nb_build_bi_views as the last step of the gold pipeline instead,
    # from this same spec, so there is one definition and one mechanism.
    views: list = []
    if views:
        body = HEADER.format(filename="V002__bi_views.sql")
        body += (
            "-- Views over the physical star schema.\n"
            "--\n"
            "-- Power BI binds HERE, never to the physical tables. That\n"
            "-- indirection is the point: a report can be reshaped by altering\n"
            "-- a view instead of migrating a table other things depend on.\n\n"
        )
        body += "\nGO\n\n".join(view_ddl(v, reporting) for v in views)
        files["V002__bi_views.sql"] = body

    return files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--specs", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    gold = load_spec(Path(args.specs), "gold")
    out = Path(args.out)
    planned = {out / name: sql for name, sql in build(gold).items()}

    if args.check:
        drifted = [p.name for p, sql in planned.items()
                   if not p.exists() or p.read_text(encoding="utf-8") != sql]
        if drifted:
            print("Generated DDL is out of date with the spec:")
            for name in drifted:
                print(f"  {name}")
            return 1
        print(f"All {len(planned)} migrations match the spec.")
        return 0

    out.mkdir(parents=True, exist_ok=True)
    for path, sql in planned.items():
        path.write_text(sql, encoding="utf-8")

    print(f"Generated {len(planned)} migrations in {out}")
    for path in sorted(planned):
        statements = planned[path].count("CREATE OR ALTER") + planned[path].count("CREATE SCHEMA")
        print(f"  {path.name}  ({statements} statements)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
