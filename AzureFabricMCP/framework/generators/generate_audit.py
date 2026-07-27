"""
Generate the data-audit notebook from the D2 spec.

Measures every entity at every layer -- rows, a money total, and whatever each
layer rejected -- and writes one row per entity per layer per run into the
warehouse, where the semantic model can report on it.

Why this exists
---------------
"Where did the rows go" was answered three separate times by hand on this
project: 2,945 order lines lost in a join, 999 orders quarantined without their
children, and gold running 4.15% short of silver. Each needed a different
ad-hoc query, and each was found only because somebody went looking.

A count alone is not enough. Value can move without rows moving -- a corrected
subtotal changes the total while every row survives -- so each layer carries a
measure as well, and the audit shows revenue surviving the pipeline rather than
just row counts.

Written to the WAREHOUSE, not a lakehouse: the semantic model reads DirectLake
from there, so an audit written anywhere else could not be reported alongside
the numbers it audits.

Usage:
    python generate_audit.py --specs ./01_demo-project --out ./01_demo-project/generated/notebooks
"""

from __future__ import annotations

import argparse
import json
import pprint
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _specs import load as load_spec

NOTEBOOK = "nb_build_audit"


def locations(scaffolding: dict) -> dict[str, tuple[str, str]]:
    """layer -> (kind, item name), so each table is read from the right place."""
    storage = scaffolding.get("storage") or {}
    medallion = ((storage.get("items") or {}).get("medallion") or {})
    resolved: dict[str, tuple[str, str]] = {}
    for layer in ("bronze", "silver", "gold"):
        entry = storage.get(layer) or medallion.get(layer) or {}
        name = entry.get("name") or medallion.get(layer, {}).get("name")
        if name:
            resolved[layer] = (entry.get("item", "lakehouse"), name)
    return resolved


def build(audit: dict, scaffolding: dict) -> str:
    target = audit["target"]
    schema, table = target["table"].split(".", 1)
    storage = locations(scaffolding)
    warehouse = storage.get("gold", ("warehouse", "wh_gold"))[1]
    default_lakehouse = storage.get("silver", ("lakehouse", "lh_silver"))[1]

    # Flattened at generation time so the notebook is a loop over data rather
    # than a nest of spec lookups.
    plan = []
    for entity in audit["entities"]:
        for layer, spec in entity["layers"].items():
            plan.append({
                "entity": entity["name"],
                "layer": layer,
                "table": spec["table"],
                "measure": spec.get("measure"),
                "filter": spec.get("filter"),
                "quarantine": spec.get("quarantine"),
                "kind": storage.get(layer, ("lakehouse", ""))[0],
                "item": storage.get(layer, ("lakehouse", ""))[1],
                "grain_changes": bool(entity.get("grain_changes")),
            })

    return f'''# Generated from dataops/02-audit.yaml -- do not edit by hand.
#
# One row per entity per layer per run: how many rows, how much value, and what
# the layer rejected. Written to the warehouse so Power BI reports on it beside
# the numbers it audits.

from datetime import datetime, timezone

from pyspark.sql import functions as F

PLAN = {pprint.pformat(plan, width=88, sort_dicts=False)}

WAREHOUSE = {warehouse!r}
TARGET_SCHEMA = {schema!r}
TARGET_TABLE = {table!r}
WRITE_MODE = {target.get("write_mode", "append")!r}

run_id = f"audit_{{datetime.now(timezone.utc):%Y%m%d_%H%M%S}}"
print(f"audit run {{run_id}}")
print()


DEFAULT_LAKEHOUSE = {default_lakehouse!r}


def read(kind, item, name):
    """Read a table from whichever item that layer actually lives in.

    The lakehouse name is NOT optional here. An unqualified read resolves
    against this notebook's default lakehouse, so every bronze table would be
    looked for in silver and come back as an error -- the audit would report
    bronze as unmeasurable while silver and gold looked fine, which reads as a
    missing layer rather than a wrong lookup.
    """
    bare = name.rsplit(".", 1)[-1]
    if kind == "warehouse":
        # The connector reads the warehouse; spark.read.table cannot.
        import com.microsoft.spark.fabric                    # noqa: F401
        schema_name = name.rsplit(".", 2)[-2] if "." in name else "dbo"
        return spark.read.synapsesql(f"{{item}}.{{schema_name}}.{{bare}}")   # noqa: F821
    if item and item != DEFAULT_LAKEHOUSE:
        return spark.read.table(f"{{item}}.{{bare}}")         # noqa: F821
    return spark.read.table(bare)                            # noqa: F821


def measure(entry):
    """rows, value and rejected for one entity at one layer."""
    df = read(entry["kind"], entry["item"], entry["table"])
    if entry["filter"]:
        df = df.filter(entry["filter"])

    rows = df.count()
    value = None
    if entry["measure"]:
        total = df.agg(F.sum(F.col(entry["measure"])).alias("v")).collect()[0]["v"]
        value = float(total) if total is not None else 0.0

    rejected = None
    if entry["quarantine"]:
        # Absent rather than zero when there is no quarantine table: a layer
        # that has never rejected anything and a layer that cannot reject are
        # different states, and reporting both as 0 hides the second.
        try:
            rejected = read(entry["kind"], entry["item"], entry["quarantine"]).count()
        except Exception:                                    # noqa: BLE001
            rejected = None

    return rows, value, rejected


records = []
for entry in PLAN:
    try:
        rows, value, rejected = measure(entry)
        records.append((run_id, entry["entity"], entry["layer"], entry["table"],
                        rows, value, rejected, entry["grain_changes"], None,
                        datetime.now(timezone.utc)))
        shown = "-" if value is None else f"{{value:,.2f}}"
        print(f"  {{entry['entity']:<14}} {{entry['layer']:<7}} "
              f"rows={{rows:>9,}}  value={{shown:>18}}  "
              f"rejected={{'-' if rejected is None else format(rejected, ',')}}")
    except Exception as exc:                                 # noqa: BLE001
        # Recorded, not raised. An audit that stops at the first unreadable
        # table tells you nothing about the layers that were fine.
        records.append((run_id, entry["entity"], entry["layer"], entry["table"],
                        None, None, None, entry["grain_changes"],
                        f"{{type(exc).__name__}}: {{exc}}"[:400],
                        datetime.now(timezone.utc)))
        print(f"  {{entry['entity']:<14}} {{entry['layer']:<7}} FAILED  {{str(exc)[:90]}}")

schema = ("run_id string, entity string, layer string, table_name string, "
          "row_count bigint, measure_value double, rejected_count bigint, "
          "grain_changes boolean, error string, measured_at timestamp")
audit_df = spark.createDataFrame(records, schema)            # noqa: F821

# The connector is overwrite-only, so appending means read, union, overwrite.
if WRITE_MODE == "append":
    try:
        import com.microsoft.spark.fabric                    # noqa: F401
        existing = spark.read.synapsesql(                    # noqa: F821
            f"{{WAREHOUSE}}.{{TARGET_SCHEMA}}.{{TARGET_TABLE}}")
        audit_df = existing.unionByName(audit_df, allowMissingColumns=True)
    except Exception:                                        # noqa: BLE001
        print("\\nno existing audit table; creating it")

import com.microsoft.spark.fabric                            # noqa: F401
from com.microsoft.spark.fabric.Constants import Constants   # noqa: F401
(audit_df.write.mode("overwrite")
    .synapsesql(f"{{WAREHOUSE}}.{{TARGET_SCHEMA}}.{{TARGET_TABLE}}"))

print(f"\\n{{len(records)}} row(s) written to "
      f"{{TARGET_SCHEMA}}.{{TARGET_TABLE}} as {{run_id}}")
'''


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--specs", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    root = Path(args.specs)
    audit = load_spec(root, "audit", track="dataops")
    scaffolding = load_spec(root, "scaffolding")

    source = build(audit, scaffolding)
    storage = locations(scaffolding)
    silver = storage.get("silver", ("lakehouse", "lh_silver"))[1]
    lakehouses = [name for kind, name in storage.values() if kind == "lakehouse"]
    lakehouses = list(dict.fromkeys([silver] + lakehouses))

    document = {
        "cells": [{"cell_type": "code", "execution_count": None, "metadata": {},
                   "outputs": [], "source": source.splitlines(keepends=True)}],
        "metadata": {
            "dependencies": {
                "environment": {"environmentId": "@@environment:env_spark@@",
                                "workspaceId": "@@workspace@@"},
                "lakehouse": {
                    "default_lakehouse": f"@@lakehouse:{silver}@@",
                    "default_lakehouse_name": silver,
                    "default_lakehouse_workspace_id": "@@workspace@@",
                    "known_lakehouses": [
                        {"id": f"@@lakehouse:{name}@@"} for name in lakehouses],
                },
            },
            "kernelspec": {"display_name": "synapse_pyspark", "name": "synapse_pyspark"},
            "language_info": {"name": "python"},
        },
        "nbformat": 4, "nbformat_minor": 5,
    }
    text = json.dumps(document, indent=1) + "\n"

    out = Path(args.out)
    target = out / f"{NOTEBOOK}.ipynb"

    if args.check:
        if not target.exists() or target.read_text(encoding="utf-8") != text:
            print(f"Generated audit notebook is out of date: {target.name}")
            return 1
        print("Audit notebook matches the spec.")
        return 0

    out.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")

    measured = sum(len(e["layers"]) for e in audit["entities"])
    print(f"Generated {target.name}")
    print(f"  {len(audit['entities'])} entities, {measured} entity/layer measurements")
    print(f"  -> {audit['target']['table']} ({audit['target'].get('write_mode', 'append')})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
