"""
Generate the DQ monitoring notebook from the D1 spec.

The pipeline already writes a DQ log as it runs. This produces the notebook that
reads the tables AFTERWARDS and evaluates them against the declared
expectations and SLAs -- which is what turns a spec full of thresholds into
something that can actually fail.

What is resolved here rather than at runtime
--------------------------------------------
- `measured_on: input` needs the upstream table. It is resolved from the bronze
  and silver mappings, so the notebook is told the name rather than rediscovering
  the layer chain in a Spark session.
- `schema_match` needs the declared columns, taken from the source registry.

Both are derivable from specs that already exist, so restating them in the
monitoring spec would be a second place for them to go stale.

Usage:
    python generate_monitoring.py --specs ./01_demo-project --out ./01_demo-project/generated/notebooks
"""

from __future__ import annotations

import argparse
import json
import pprint
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _specs import load as load_spec

NOTEBOOK = "nb_dq_monitor"


def _source_of(entity: dict) -> str | None:
    source = entity.get("source")
    if isinstance(source, str):
        return source
    if isinstance(source, dict):
        return source.get("table") or source.get("name")
    return None


def registry_columns(sources: dict) -> dict[str, list[str]]:
    """Map 'system.entity' -> declared column names from the source registry.

    Bronze tables declare `columns: passthrough` -- they take whatever the
    source has -- so the only authoritative list of what a bronze table SHOULD
    contain is the registry. Reading it from there means schema_match compares
    against the contract with the upstream system rather than against a copy
    of it that can drift.
    """
    columns: dict[str, list[str]] = {}
    for system in sources.get("sources") or []:
        for entity in system.get("entities") or []:
            declared = [c.get("name") or c.get("source")
                        for c in entity.get("columns") or entity.get("schema") or []]
            declared = [c for c in declared if c]
            if declared:
                columns[f"{system['name']}.{entity['name']}"] = declared
    return columns


def resolve_sources(monitoring: dict, sources: dict, bronze: dict,
                    silver: dict, gold: dict) -> dict:
    """Fill in `source` and `expected_columns` from the specs that already hold them.

    Nothing is overwritten: an expectation that states these explicitly keeps
    what it declared. Deriving them avoids a second copy that can go stale --
    the failure mode being a schema_match that passes against last year's
    column list.
    """
    upstream: dict[str, str] = {}
    columns: dict[str, list[str]] = {}
    registry = registry_columns(sources)

    # Bronze takes its shape from the source registry via source_entity.
    for entity in bronze.get("tables") or []:
        target = entity.get("target")
        reference = entity.get("source_entity")
        if target and reference and registry.get(reference):
            columns[target] = registry[reference]

    for entity in silver.get("tables") or []:
        target, source = entity.get("target"), _source_of(entity)
        if target and source:
            upstream[target] = source
        declared = [c.get("target") or c.get("source")
                    for c in entity.get("columns") or []
                    if isinstance(c, dict)]
        if declared:
            columns[target] = [c for c in declared if c]

    for entity in (gold.get("dimensions") or []) + (gold.get("facts") or []):
        target, source = entity.get("name"), _source_of(entity)
        if target and source:
            upstream[target] = source

    for expectation in monitoring["expectations"]:
        table = expectation["table"]
        if expectation.get("source") is None and upstream.get(table):
            expectation["source"] = upstream[table]
        if not expectation.get("expected_columns") and columns.get(table):
            expectation["expected_columns"] = columns[table]

    return monitoring


def layer_storage(scaffolding: dict) -> dict[str, tuple[str, str]]:
    """layer -> (kind, item name), e.g. 'gold' -> ('warehouse', 'wh_gold')."""
    storage = scaffolding.get("storage") or {}
    resolved: dict[str, tuple[str, str]] = {}
    medallion = ((storage.get("items") or {}).get("medallion") or {})
    for layer in ("bronze", "silver", "gold"):
        entry = storage.get(layer) or medallion.get(layer) or {}
        kind = entry.get("item", "lakehouse")
        name = entry.get("name") or medallion.get(layer, {}).get("name")
        if name:
            resolved[layer] = (kind, name)
    return resolved


def build(monitoring: dict, scaffolding: dict) -> str:
    """The notebook body. Thin: the rules live in ttfabric.monitoring."""
    expectations = monitoring["expectations"]
    slas = monitoring.get("slas") or []
    enforcement = monitoring.get("enforcement") or {}
    results_table = monitoring.get("results", {}).get("table", "dq_run_log")
    # The results table is written unqualified so it lands in the notebook's
    # own lakehouse, matching how every other layer writes it.
    results_table = results_table.rsplit(".", 1)[-1] + "_monitor"

    # Where each table physically lives. Derived from the layer each
    # expectation declares plus the scaffolding topology, so the notebook is
    # told rather than guessing -- and a mesh topology needs no change here.
    storage = layer_storage(scaffolding)
    locations: dict[str, tuple[str, str]] = {}
    for expectation in expectations:
        entry = storage.get(expectation["layer"])
        if entry:
            locations[expectation["table"].rsplit(".", 1)[-1]] = entry
        if expectation.get("source"):
            # An upstream table sits one layer back.
            back = {"silver": "bronze", "gold": "silver"}.get(expectation["layer"])
            if back and storage.get(back):
                locations[expectation["source"].rsplit(".", 1)[-1]] = storage[back]
    for sla in slas:
        bare = sla["table"].rsplit(".", 1)[-1]
        if bare not in locations:
            # bi views and dbo tables both live in the gold item.
            locations[bare] = storage.get("gold", ("warehouse", "wh_gold"))
    # Referenced parents (referential_integrity) share their child's layer.
    for expectation in expectations:
        entry = storage.get(expectation["layer"])
        for check in expectation["checks"]:
            if check.get("references") and entry:
                locations.setdefault(
                    check["references"].rsplit(".", 2)[0].rsplit(".", 1)[-1], entry)

    # A reconciliation names two tables, and the interesting ones sit in
    # DIFFERENT layers -- comparing gold against the silver it was built from is
    # the whole point. Each side is placed by the layer of the expectation that
    # declares it, falling back to the layer whose own expectation names it.
    by_table = {e["table"].rsplit(".", 1)[-1]: e["layer"] for e in expectations}
    for expectation in expectations:
        for check in expectation["checks"]:
            for side in ("left", "right"):
                table = (check.get(side) or {}).get("table")
                if not table:
                    continue
                bare = table.rsplit(".", 1)[-1]
                layer = by_table.get(bare, expectation["layer"])
                if storage.get(layer):
                    locations.setdefault(bare, storage[layer])

    default_lakehouse_name = (storage.get("silver") or ("lakehouse", "lh_silver"))[1]

    return f'''# Generated from dataops/01-monitoring.yaml -- do not edit by hand.
#
# Evaluates every declared expectation and SLA against what actually landed,
# then applies the enforcement mode for this environment.
#
# The rules live in ttfabric.monitoring; this notebook only supplies the tables
# and decides what to do with the verdicts.

from datetime import datetime, timezone
import uuid

from pyspark.sql import functions as F
from ttfabric.monitoring import (
    run_expectations, evaluate_slas, FAIL, ERROR, WARN,
)

# `env` comes from the parameters cell above, overridden at run time by the
# pipeline or by tools/run_monitor.py.
try:
    environment = str(env).strip() or "dev"      # noqa: F821  (notebook parameter)
except NameError:
    environment = "dev"

EXPECTATIONS = {pprint.pformat(expectations, width=88, sort_dicts=False)}

SLAS = {pprint.pformat(slas, width=88, sort_dicts=False)}

ENFORCEMENT = {pprint.pformat(enforcement, width=88, sort_dicts=False)}

# Where each table lives. The monitor spans three storage items of two
# different kinds; nothing else in the pipeline crosses all of them at once.
LOCATIONS = {pprint.pformat(locations, width=88, sort_dicts=False)}
DEFAULT_LAKEHOUSE = {default_lakehouse_name!r}

RESULTS_TABLE = {results_table!r}
run_id = f"mon_{{datetime.now(timezone.utc):%Y%m%d_%H%M%S}}"

print(f"environment {{environment}}   run {{run_id}}")
print(f"{{len(EXPECTATIONS)}} tables, "
      f"{{sum(len(e['checks']) for e in EXPECTATIONS)}} checks, {{len(SLAS)}} SLAs")
print()


def read(name):
    """Resolve a table name to a DataFrame, wherever that layer actually lives.

    The monitor spans all three layers, and they are NOT in one place:

      bronze   lh_bronze    a different lakehouse -- must be qualified
      silver   lh_silver    this notebook's default -- unqualified works
      gold     wh_gold      a WAREHOUSE, which spark.read.table cannot reach
                            at all; it needs the Fabric DW connector

    Reading everything unqualified silently resolves against the default
    lakehouse, so every bronze and gold check fails as TABLE_OR_VIEW_NOT_FOUND
    -- which looks like missing data rather than a wrong lookup.
    """
    bare = name.rsplit(".", 1)[-1]
    kind, item = LOCATIONS.get(bare, ("lakehouse", None))

    if kind == "warehouse":
        # Lazy import: the connector registers spark.read.synapsesql as a side
        # effect, and is unavailable until it has been imported at least once.
        import com.microsoft.spark.fabric                  # noqa: F401
        from com.microsoft.spark.fabric.Constants import Constants  # noqa: F401
        schema = name.rsplit(".", 2)[-2] if "." in name else "dbo"
        return spark.read.synapsesql(f"{{item}}.{{schema}}.{{bare}}")   # noqa: F821

    if item and item != DEFAULT_LAKEHOUSE:
        return spark.read.table(f"{{item}}.{{bare}}")      # noqa: F821
    return spark.read.table(bare)                          # noqa: F821


# Previous row counts, so row_count_delta has something to compare against.
# Absent on a first run, which the rule reports as "not measurable" rather
# than as a breach.
previous_rows = {{}}
try:
    history = spark.read.table(RESULTS_TABLE)             # noqa: F821
    latest = history.filter(F.col("rule_id") == "row_count_delta") \\
        .groupBy("table_name").agg(F.max("run_id").alias("run_id"))
    for row in history.join(latest, ["table_name", "run_id"]).collect():
        if row["rows"] is not None:
            previous_rows[row["table_name"]] = row["rows"]
except Exception as exc:
    print(f"no monitoring history yet ({{type(exc).__name__}}); "
          f"row_count_delta will be skipped on this run")

run = run_expectations(read, EXPECTATIONS, previous_rows=previous_rows)
run.results.extend(evaluate_slas(read, SLAS))

for result in run.results:
    print(result)

counts = run.counts()
print()
print("  ".join(f"{{status}}={{count}}" for status, count in sorted(counts.items())))

# --- persist -------------------------------------------------------------
rows = [(run_id, r.check_id, r.table, r.layer, r.rule, r.severity, r.status,
         float(r.measured) if r.measured is not None else None,
         float(r.limit) if r.limit is not None else None,
         int(r.rows) if r.rows is not None else None,
         r.detail, datetime.now(timezone.utc))
        for r in run.results]

schema = ("run_id string, check_id string, table_name string, layer string, "
          "rule_id string, severity string, status string, measured double, "
          "limit_value double, rows bigint, detail string, checked_at timestamp")
# mergeSchema: this is an append-only log, and adding a column to it should not
# be what stops monitoring from running. Delta otherwise rejects the whole write
# on any schema difference.
spark.createDataFrame(rows, schema).write.mode("append") \\
    .option("mergeSchema", "true") \\
    .saveAsTable(RESULTS_TABLE)                            # noqa: F821
print(f"\\n{{len(rows)}} results appended to {{RESULTS_TABLE}}")

# --- enforce -------------------------------------------------------------
policy = ENFORCEMENT.get(environment, {{"mode": "warn", "block_on": []}})
blocking = run.blocking(policy.get("block_on") or [])

print(f"\\nenforcement for {{environment}}: mode={{policy.get('mode')}} "
      f"block_on={{policy.get('block_on') or 'nothing'}}")

if blocking:
    for result in blocking:
        print(f"  BLOCKING  {{result}}")
    # Raised so the pipeline stops. In an environment whose block_on is empty
    # this never fires, which is the point of declaring it per environment.
    raise RuntimeError(
        f"{{len(blocking)}} blocking breach(es) in {{environment}}: "
        + ", ".join(r.check_id for r in blocking))

breached = [r for r in run.results if r.status in (FAIL, ERROR, WARN)]
if breached:
    print(f"\\n{{len(breached)}} breach(es) recorded, none blocking in {{environment}}.")
else:
    print("\\nall checks passed")
'''


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--specs", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    root = Path(args.specs)
    monitoring = load_spec(root, "monitoring", track="dataops")
    monitoring = resolve_sources(
        monitoring, load_spec(root, "sources"), load_spec(root, "bronze"),
        load_spec(root, "silver"), load_spec(root, "gold"))

    unresolved = [e["table"] for e in monitoring["expectations"]
                  if not e.get("source")
                  and any(c.get("measured_on") == "input" for c in e["checks"])]
    if unresolved:
        print("  ERROR  these have measured_on: input but no upstream table, "
              "and none could be resolved from the layer specs:")
        for table in unresolved:
            print(f"           {table}")
        print("         Add `source:` to the expectation.")
        return 1

    scaffolding = load_spec(root, "scaffolding")
    source = build(monitoring, scaffolding)

    # Bindings are environment-specific GUIDs, so placeholders are emitted and
    # push_items.py resolves them against the target workspace -- the same
    # approach the layer notebooks use, so one artefact promotes unchanged.
    #
    # The default lakehouse is SILVER, not bronze or gold: the monitor reads
    # tables unqualified, and silver is the only lakehouse from which every
    # layer's tables are reachable in this topology.
    storage = layer_storage(scaffolding)
    default_lakehouse = (storage.get("silver") or ("lakehouse", "lh_silver"))[1]

    # EVERY lakehouse the monitor reads must be declared here, not just the
    # default: `spark.read.table("lh_bronze.x")` resolves only against a known
    # lakehouse, and otherwise fails as TABLE_OR_VIEW_NOT_FOUND -- which reads
    # as missing data rather than a missing binding.
    lakehouses = [name for kind, name in storage.values() if kind == "lakehouse"]
    lakehouses = list(dict.fromkeys([default_lakehouse] + lakehouses))

    # A cell tagged "parameters" is where Fabric injects run parameters. Without
    # the tag, executionData.parameters are accepted by the API and silently
    # ignored -- so the monitor would apply dev's policy in every environment
    # while appearing to be told otherwise.
    parameter_cell = {
        "cell_type": "code", "execution_count": None,
        "metadata": {"tags": ["parameters"]}, "outputs": [],
        "source": [
            "# Injected by the pipeline or CI. Defaults to dev so a manual run\n",
            "# is never the strict one.\n",
            'env = "dev"\n',
        ],
    }

    document = {
        "cells": [parameter_cell,
                  {"cell_type": "code", "execution_count": None, "metadata": {},
                   "outputs": [], "source": source.splitlines(keepends=True)}],
        "metadata": {
            "dependencies": {
                # Carries the ttfabric wheel, without which the import fails.
                "environment": {"environmentId": "@@environment:env_spark@@",
                                "workspaceId": "@@workspace@@"},
                "lakehouse": {
                    "default_lakehouse": f"@@lakehouse:{default_lakehouse}@@",
                    "default_lakehouse_name": default_lakehouse,
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
            print(f"Generated monitoring notebook is out of date: {target.name}")
            return 1
        print("Monitoring notebook matches the spec.")
        return 0

    out.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")

    checks = sum(len(e["checks"]) for e in monitoring["expectations"])
    resolved = sum(1 for e in monitoring["expectations"] if e.get("source"))
    print(f"Generated {target.name}")
    print(f"  {len(monitoring['expectations'])} tables, {checks} checks, "
          f"{len(monitoring.get('slas') or [])} SLAs")
    print(f"  {resolved} upstream sources resolved from the layer specs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
