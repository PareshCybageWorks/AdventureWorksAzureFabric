"""
Generate Fabric PySpark notebooks from a project's mapping specs.

One notebook per silver table and one per gold item. Generated notebooks are
build output, never edited by hand -- the `notebook-lint` CI gate regenerates
them and fails if the result differs from what is committed, so the spec stays
the only place behaviour is defined.

The generated code CALLS the framework cleansing library rather than inlining
logic. A notebook is therefore a thin, readable sequence of rule invocations,
and a fix to a rule lands everywhere at once instead of needing to be applied
across every entity that copied it.

Output is deterministic: cells are emitted in spec order, JSON keys are sorted,
and no execution counts or kernel metadata are written. Regenerating without a
spec change produces a byte-identical file, which is what makes the lint gate
meaningful.

Usage:
    python generate_notebooks.py --specs ./specs --out ./generated/notebooks
    python generate_notebooks.py --specs ./specs --out ./generated/notebooks --check

`--check` regenerates into memory and reports drift without writing.
"""

from __future__ import annotations

import argparse
import json
import pprint
import sys
import textwrap
from pathlib import Path

import yaml

# Sibling import: generators are run as files, not as a package.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _specs import load as load_spec

# Rules that legitimately CHANGE THE ROW COUNT upward. The silver layer
# identity count(in) == count(kept) + count(rejected) does not hold across
# these, by design rather than by defect. Kept in step with the same set in
# tools/dryrun.py and with D2's `grain_changes`.
ROW_MULTIPLYING_RULES = {"explode_json_array", "extend_calendar"}

BANNER = (
    "GENERATED FILE -- DO NOT EDIT.\n"
    "Produced by framework/generators/generate_notebooks.py from the project "
    "spec set. Edit the spec and regenerate; hand edits are overwritten and "
    "will fail the notebook-lint gate."
)


# ---------------------------------------------------------------------------
# Notebook assembly
# ---------------------------------------------------------------------------

def markdown_cell(text: str) -> dict:
    return {"cell_type": "markdown", "metadata": {}, "source": text.splitlines(keepends=True)}


def code_cell(code: str) -> dict:
    return {
        "cell_type": "code",
        "execution_count": None,
        "metadata": {},
        "outputs": [],
        "source": code.splitlines(keepends=True),
    }


# Lakehouse bindings are environment-specific GUIDs, so generation emits
# placeholders and deploy/push_items.py resolves them against the target
# workspace -- the same approach the pipeline definitions use, for the same
# reason: one artefact must promote across environments unchanged.
LAKEHOUSE_REF = "@@lakehouse:{name}@@"
ENVIRONMENT_REF = "@@environment:{name}@@"
WORKSPACE_REF = "@@workspace@@"

# The Spark Environment carrying the ttfabric wheel. Declared in the
# scaffolding spec under workspace_folders/0_config.
SPARK_ENVIRONMENT = "env_spark"


def notebook(cells: list[dict], default_lakehouse: str | None = None,
             known_lakehouses: list[str] | None = None,
             environment: str | None = SPARK_ENVIRONMENT) -> dict:
    """Assemble a notebook and bind it to its Environment and lakehouses.

    ENVIRONMENT carries the ttfabric wheel, so the notebook can `import
    ttfabric.cleansing` natively. This replaced appending
    /lakehouse/default/Files/framework to sys.path, which resolved through the
    default-lakehouse binding and therefore failed on a freshly created
    notebook that had not finished propagating.

    DEFAULT LAKEHOUSE is where an unqualified write lands, so it is set to the
    item the notebook WRITES to, never the one it reads from.
    """
    metadata = {
        "kernelspec": {"display_name": "synapse_pyspark", "name": "synapse_pyspark"},
        "language_info": {"name": "python"},
    }

    dependencies: dict = {}

    if environment:
        dependencies["environment"] = {
            "environmentId": ENVIRONMENT_REF.format(name=environment),
            "workspaceId": WORKSPACE_REF,
        }

    if default_lakehouse:
        known = known_lakehouses or [default_lakehouse]
        dependencies["lakehouse"] = {
            "default_lakehouse": LAKEHOUSE_REF.format(name=default_lakehouse),
            "default_lakehouse_name": default_lakehouse,
            "default_lakehouse_workspace_id": WORKSPACE_REF,
            "known_lakehouses": [
                {"id": LAKEHOUSE_REF.format(name=name)} for name in known
            ],
        }

    if dependencies:
        metadata["dependencies"] = dependencies

    return {
        "cells": cells,
        "metadata": metadata,
        "nbformat": 4,
        "nbformat_minor": 5,
    }


def render(nb: dict) -> str:
    # sort_keys makes regeneration byte-stable, which the lint gate depends on.
    return json.dumps(nb, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def wrap_comment(text: str, width: int = 72) -> str:
    """Render a spec description as a wrapped Python comment block.

    Wraps on word boundaries. Naive slicing splits words across lines, which
    makes the rationale carried down from the spec harder to read than not
    carrying it at all.
    """
    collapsed = " ".join((text or "").split())
    if not collapsed:
        return ""
    return "".join(f"#   {line}\n" for line in textwrap.wrap(collapsed, width=width))


# Rules whose arguments live on the column definition rather than in the rule's
# own `params`. Without this bridge the generator emits a call missing a
# required argument, and the notebook fails at runtime rather than at build
# time -- so anything added here must also be covered by a validator check.
COLUMN_SOURCED_PARAMS = {
    "enforce_allowed_values": ("allowed_values", "values"),
}


def enrich_params(rule: dict, table: dict) -> dict:
    """Merge a rule's declared params with values held on its target column."""
    params = dict(rule.get("params", {}))

    binding = COLUMN_SOURCED_PARAMS.get(rule["fn"])
    if not binding:
        return params

    column_field, param_name = binding
    if param_name in params:
        return params

    target = params.get("column")
    for column in table.get("columns", []):
        if column.get("target") == target and column_field in column:
            params[param_name] = column[column_field]
            break
    else:
        raise ValueError(
            f"{rule['fn']} on {table['target']}.{target} needs '{column_field}' "
            f"declared on that column in the mapping spec"
        )
    return params


# ---------------------------------------------------------------------------
# Shared preamble
# ---------------------------------------------------------------------------

# Which layer each layer reads FROM. Under a per-layer storage split this is
# no longer the same item the layer writes to, so both must be resolved.
_UPSTREAM = {"bronze": None, "silver": "bronze", "gold": "silver"}


def storage_items(platform: dict) -> dict[str, str]:
    """Resolve the storage item name for each layer from the active topology.

    Under `medallion` the items are per layer (lh_bronze, lh_silver, wh_gold).
    Under `mesh` a single per-domain item holds all three, so every layer
    resolves to the same name.
    """
    pattern = platform["topology"]["pattern"]
    items = platform["storage"]["items"]

    if pattern == "mesh":
        domain = platform["domains"][0]["name"]
        name = items["mesh"]["per_domain"]["name"].format(domain=domain)
        return {layer: name for layer in ("bronze", "silver", "gold")}

    return {layer: items["medallion"][layer]["name"] for layer in ("bronze", "silver", "gold")}


def preamble_cells(platform: dict, layer: str, table: str) -> list[dict]:
    items = storage_items(platform)
    target_item = items[layer]
    upstream_layer = _UPSTREAM[layer]
    source_item = items[upstream_layer] if upstream_layer else target_item

    # When the layers live in separate items, a read has to name the item it
    # is reading from. Attach both to the notebook in Fabric; the default is
    # the one this layer WRITES to, so an unqualified write cannot land in the
    # wrong lakehouse.
    cross_item = source_item != target_item

    note = (
        f"# Reads from {source_item}, writes to {target_item}. Both must be\n"
        f"# attached to this notebook; {target_item} must be the DEFAULT so an\n"
        f"# unqualified write cannot land in the wrong item.\n"
        if cross_item else ""
    )

    setup = f'''\
# Parameters -- overridden per environment by the deployment pipeline.
# See 05-deployment.yaml `parameterisation`.
{note}target_item = "{target_item}"
source_item = "{source_item}"
environment = "dev"
dq_failure_action = "warn"

import sys
from datetime import datetime

from pyspark.sql import functions as F

from ttfabric.cleansing import RuleContext, get_rule
from ttfabric.quality import DQRunLog

load_id = f"load_{{datetime.utcnow():%Y%m%d_%H%M%S}}"

def resolve_table(name: str):
    """Resolve a spec table reference to a DataFrame.

    Deliberately UNQUALIFIED, so the read lands in the default lakehouse.

    Rules reference tables in their OWN layer -- enforce_referential_integrity
    against dim_products, recompute_total_from_lines against fct_order_items --
    and those peers live in the item this notebook writes to, not the one it
    reads its source from. Qualifying with source_item sent them to
    lh_bronze.dim_products, which does not and should not exist.

    The single cross-item read, this table's own bronze source, is qualified
    explicitly at the call site instead.
    """
    bare = name.split(".")[-1]
    return spark.read.table(bare)

ctx = RuleContext(
    spark=spark,
    load_id=load_id,
    environment=environment,
    table="{table}",
    resolve_table=resolve_table,
    apply_masking=(environment in ("uat", "prod")),
)

dq = DQRunLog(spark, load_id=load_id, layer="{layer}", table_name="{table}")
print(f"load_id={{load_id}}  environment={{environment}}  table={table}")
'''

    # Gold writes must never be unqualified. A notebook's default lakehouse
    # cannot be a Warehouse, so gold's default is the lakehouse it READS from --
    # meaning an unqualified saveAsTable lands gold tables in the silver item,
    # under names one letter apart from the silver ones. The gold target routes
    # every write to the warehouse instead.
    if layer == "gold":
        gold_config = platform["storage"]["gold"]
        setup += f'''
from ttfabric.warehouse import gold_target

gold = gold_target(
    spark,
    warehouse="{items["gold"]}",
    schema="{gold_config["schemas"]["physical"]}",
    write_mode="{gold_config["write_mode"]}",
)
'''

    return [code_cell(setup)]


# ---------------------------------------------------------------------------
# Bronze notebooks
# ---------------------------------------------------------------------------

_SPARK_TYPE_NAMES = {
    "string": "StringType()",
    "integer": "IntegerType()",
    "bigint": "LongType()",
    "boolean": "BooleanType()",
    "date": "DateType()",
    "timestamp": "StringType()",   # parsed after load; see below
}


def _schema_literal(entity: dict) -> str:
    """Render an explicit Spark schema from the source registry.

    Explicit rather than inferred because 02-sources.yaml sets
    `infer_schema: false`. Inference reads the file twice, and worse, it lets
    the schema change silently when the data changes -- which is precisely the
    drift the landing checks exist to catch.

    Timestamps land as strings and are parsed in a later step, so a malformed
    value becomes a null the silver rules can quarantine rather than an
    exception that fails the whole load.
    """
    fields = []
    for column in entity["columns"]:
        declared = column["type"]
        spark_type = _SPARK_TYPE_NAMES.get(declared, "StringType()")
        if declared == "decimal":
            spark_type = "StringType()"    # parsed in silver cast_types
        fields.append(
            f'    StructField("{column["name"]}", {spark_type}, '
            f'{not column.get("nullable", True) is True or True}),'
        )
    body = "\n".join(fields)
    return f"StructType([\n{body}\n])"


def bronze_notebook(platform: dict, mapping: dict, entity: dict,
                    source: dict, defaults: dict) -> dict:
    target = mapping["target"]
    entity_name = entity["name"]
    source_name = source["name"]

    cells = [
        markdown_cell(
            f"# Bronze -- `{target}`\n\n"
            f"Landing only. No deduplication, no filtering, no business logic.\n\n"
            f"**Source:** `{source_name}.{entity_name}`  \n"
            f"**File:** `{entity['file_pattern']}`  \n"
            f"**Watermark:** `{mapping.get('watermark', {}).get('column')}`  \n"
            f"**Load pattern:** `{mapping.get('load_pattern')}`\n\n"
            f"> Every upstream defect survives this layer intact. If bronze "
            f"silently fixed anything, a silver bug would be indistinguishable "
            f"from an upstream change and replay would not reproduce the "
            f"original state.\n\n"
            f"> {BANNER}\n"
        )
    ]
    cells += preamble_cells(platform, "bronze", target)

    cells.append(code_cell(
        f'# ---- Declared schema ---------------------------------------------\n'
        f'from pyspark.sql.types import (StructType, StructField, StringType,\n'
        f'                               IntegerType, LongType, BooleanType, DateType)\n'
        f'\n'
        f'schema = {_schema_literal(entity)}\n'
    ))

    options = source["connection"].get("options", {})
    landing = platform["storage"]["bronze"]["landing_path"]
    folder = landing.split("/ingest_date=")[0].format(
        source=source_name, entity=entity_name
    )

    cells.append(code_cell(
        f'# ---- Read the landed file ----------------------------------------\n'
        f'landing_path = "Files/{folder.split("Files/", 1)[-1]}"\n'
        f'\n'
        f'df = (spark.read\n'
        f'    .option("header", "{str(options.get("header", True)).lower()}")\n'
        f'    .option("delimiter", "{options.get("delimiter", ",")}")\n'
        f'    .option("encoding", "{options.get("encoding", "utf-8")}")\n'
        f'    .option("quote", \'{options.get("quote", chr(34))}\')\n'
        f'    .option("dateFormat", "yyyy-MM-dd")\n'
        f'    .option("timestampFormat", "yyyy-MM-dd HH:mm:ss")\n'
        f'    .schema(schema)\n'
        f'    .csv(landing_path))\n'
        f'\n'
        f'rows_in = df.count()\n'
        f'dq.record_input(rows_in)\n'
        f'print(f"read {{rows_in:,}} rows from {{landing_path}}")\n'
    ))

    cells.append(code_cell(
        '# ---- Landing checks ----------------------------------------------\n'
        '# These test whether the file ARRIVED correctly -- not whether its\n'
        '# contents are any good, which is silver\'s job.\n'
        '#\n'
        '# The header is re-read WITHOUT the declared schema. Reading it from\n'
        '# `df.columns` would return the schema\'s own names, so the check would\n'
        '# compare the schema to itself and could never fail -- while the\n'
        '# schema, applied positionally, silently loaded values into the wrong\n'
        '# columns. That failure is invisible whenever the mismatched columns\n'
        '# share a type.\n'
        'expected = [f.name for f in schema.fields]\n'
        'actual = (spark.read\n'
        '    .option("header", "true")\n'
        '    .csv(landing_path)\n'
        '    .columns)\n'
        '\n'
        'if actual != expected:\n'
        '    raise AssertionError(\n'
        '        f"header_matches_registry failed.\\n"\n'
        '        f"  registry: {expected}\\n"\n'
        '        f"  file:     {actual}\\n"\n'
        '        f"Order matters -- the schema is applied positionally."\n'
        '    )\n'
        'if rows_in == 0:\n'
        '    raise AssertionError("row_count_not_zero failed: a zero-row file "\n'
        '                         "almost always means a broken export")\n'
        'print("landing checks passed")\n'
    ))

    watermark = (mapping.get("watermark") or {}).get("column")
    initial = (mapping.get("watermark") or {}).get("initial_value", "1900-01-01T00:00:00Z")

    if watermark and mapping.get("load_pattern") == "incremental":
        cells.append(code_cell(
            f'# ---- Incremental watermark ---------------------------------------\n'
            f'# Bronze appends and never overwrites, so without this filter every\n'
            f'# re-run re-appends the entire source and silently multiplies the\n'
            f'# table. Deduplication downstream would hide it, which is worse than\n'
            f'# failing -- the counts stay plausible while bronze stops being a\n'
            f'# faithful record of what arrived.\n'
            f'from pyspark.sql.utils import AnalysisException\n'
            f'\n'
            f'try:\n'
            f'    high_water = (spark.read.table("{target}")\n'
            f'        .agg(F.max("{watermark}")).collect()[0][0])\n'
            f'except AnalysisException:\n'
            f'    high_water = None          # first load; take everything\n'
            f'\n'
            f'if high_water:\n'
            f'    before = rows_in\n'
            f'    df = df.filter(F.col("{watermark}") > F.lit(high_water))\n'
            f'    rows_in = df.count()\n'
            f'    print(f"watermark {watermark} > {{high_water}}: "\n'
            f'          f"{{before:,}} -> {{rows_in:,}} new rows")\n'
            f'else:\n'
            f'    print("no high-water mark; loading all {{rows_in:,}} rows".format(rows_in=rows_in))\n'
            f'\n'
            f'dq.record_input(rows_in)\n'
            f'\n'
            f'if rows_in == 0:\n'
            f'    print("nothing new to land")\n'
            f'    dq.record_output(0)\n'
            f'    dq.flush()\n'
            f'    mssparkutils.notebook.exit("no new rows")\n'
        ))

    audit = platform["audit_columns"]["bronze"]
    audit_lines = "".join(
        f'    .withColumn("{c["name"]}", '
        + (f'F.current_timestamp())\n' if c["source"] == "current_timestamp()"
           else f'F.lit("{source_name}"))\n' if c["source"] == "spec.source_system"
           else f'F.lit(load_id))\n' if c["source"] == "runtime.load_id"
           else f'F.input_file_name())\n')
        for c in audit
    )

    # A FULL snapshot must be idempotent per ingest_date, or a retry doubles it.
    #
    # This cost real data. Bronze is append -- the contract permits nothing else,
    # because overwrite would destroy the replayable history that is the whole
    # point of the layer. But `append` plus `load_pattern: full` means a
    # pipeline that RETRIES a failed notebook lands a second complete snapshot,
    # and the pipeline then reports success because the retry succeeded.
    #
    # It happened on the first real run of this framework against a contended
    # capacity: four of fifteen bronze notebooks hit a Spark capacity limit,
    # were retried by the pipeline, and each appended a second full copy.
    # Nothing failed. It surfaced two layers later as a gold fan-out --
    # "2,000 fact rows became 4,000" -- blamed on the dimension.
    #
    # Dynamic partition overwrite replaces only the partitions the DataFrame
    # actually carries, so a re-run of today replaces today and leaves every
    # earlier ingest_date untouched. History across dates is preserved; a retry
    # within a date is idempotent.
    partition_by = defaults.get("partition_by") or []
    write_mode = defaults.get("write_mode", "append")
    full_snapshot = mapping.get("load_pattern") == "full" and "ingest_date" in partition_by

    if full_snapshot:
        write_cell = (
            f'# `ingest_date` is the partition a full snapshot owns. Stamped\n'
            f'# here rather than read from the landing path, so a re-run on the\n'
            f'# same day targets the same partition.\n'
            f'out = out.withColumn("ingest_date", F.current_date())\n'
            f'\n'
            f'# IDEMPOTENT per ingest_date. Not plain append: a pipeline retry\n'
            f'# would otherwise land a second full snapshot and report success.\n'
            f'# No `overwriteSchema` here. Delta rejects it outright in dynamic\n'
            f'# partition overwrite mode -- DELTA_OVERWRITE_SCHEMA_WITH_DYNAMIC_\n'
            f'# PARTITION_OVERWRITE -- and it would be wrong regardless: F3 sets\n'
            f'# on_schema_drift: fail, so a changed source schema must STOP the\n'
            f'# load rather than quietly rewrite the table around it.\n'
            f'(out.write.mode("overwrite")\n'
            f'    .option("partitionOverwriteMode", "dynamic")\n'
            f'    .partitionBy("ingest_date")\n'
            f'    .format("delta").saveAsTable("{target}"))\n'
        )
    else:
        write_cell = (
            f'out.write.mode("{write_mode}") \\\n'
            f'    .format("delta").saveAsTable("{target}")\n'
        )

    cells.append(code_cell(
        f'# ---- Audit columns and write -------------------------------------\n'
        f'# Injected from 00-platform.yaml `audit_columns.bronze`, so the\n'
        f'# provenance contract is identical across every entity.\n'
        f'out = (df\n{audit_lines})\n'
        f'\n'
        f'{write_cell}'
        f'\n'
        f'dq.record_output(rows_in)\n'
        f'print(f"landed {{rows_in:,}} rows into {target}")\n'
        f'dq.flush()\n'
    ))

    return notebook(cells, default_lakehouse="lh_bronze", known_lakehouses=["lh_bronze"])


# ---------------------------------------------------------------------------
# Silver notebooks
# ---------------------------------------------------------------------------

def silver_notebook(platform: dict, table: dict, defaults: dict) -> dict:
    target = table["target"]
    source = table["source"]

    cells = [
        markdown_cell(
            f"# Silver -- `{target}`\n\n"
            f"{table.get('description', '')}\n\n"
            f"**Source:** `{source}`  \n"
            f"**Business key:** `{', '.join(table.get('business_key', []))}`  \n"
            f"**Load pattern:** `{table.get('load_pattern', 'full_refresh')}`\n\n"
            f"> {BANNER}\n"
        )
    ]
    cells += preamble_cells(platform, "silver", target)

    # ---- read -----------------------------------------------------------
    depends = table.get("depends_on", [])
    depends_note = (
        f"\n# Depends on {', '.join(depends)} being built first -- the spec's\n"
        f"# depends_on establishes this ordering.\n" if depends else "\n"
    )
    cells.append(code_cell(
        f'# ---- Read bronze -------------------------------------------------{depends_note}'
        f'df = spark.read.table(f"{{source_item}}.{source}")\n'
        f'rows_in = df.count()\n'
        f'dq.record_input(rows_in)\n'
        f'print(f"read {{rows_in:,}} rows from {source}")\n'
    ))

    # ---- column spec ----------------------------------------------------
    # pformat, not json.dumps: JSON renders booleans and nulls as `false`,
    # `true` and `null`, which are not Python literals. Emitting them into a
    # notebook produces NameError at run time -- and only at run time, because
    # the generated file is still perfectly valid JSON.
    columns_literal = pprint.pformat(table.get("columns", []), indent=4,
                                     width=88, sort_dicts=False)
    cells.append(code_cell(
        f'# ---- Column mapping ----------------------------------------------\n'
        f'# Lifted verbatim from mappings/silver.yaml so the notebook is\n'
        f'# self-contained and auditable without opening the spec.\n'
        f'columns = {columns_literal}\n'
    ))

    # ---- rules ----------------------------------------------------------
    cells.append(code_cell(
        '# ---- Cleansing rules ---------------------------------------------\n'
        '# Each rule returns kept and rejected rows. Rejected rows accumulate\n'
        '# into the quarantine frame so nothing is lost without a reason.\n'
        'quarantine = None\n'
        '\n'
        'def apply(result):\n'
        '    """Collect rejects and carry the kept frame forward."""\n'
        '    global quarantine, df\n'
        '    if result.rejected is not None and not result.rejected.isEmpty():\n'
        '        quarantine = (result.rejected if quarantine is None\n'
        '                      else quarantine.unionByName(result.rejected,\n'
        '                                                  allowMissingColumns=True))\n'
        '    if result.corrected_count:\n'
        '        dq.record_corrected(result.corrected_count)\n'
        '    df = result.kept\n'
        '    return result\n'
    ))

    for rule in table.get("rules", []):
        fn = rule["fn"]
        params = enrich_params(rule, table)

        # Rules that operate over the whole column spec receive it directly.
        if fn in ("cast_types", "apply_transforms"):
            call = f'get_rule("{fn}")(df, ctx, columns=columns)'
        else:
            rendered = ", ".join(f"{k}={v!r}" for k, v in params.items())
            call = f'get_rule("{fn}")(df, ctx{", " + rendered if rendered else ""})'

        comment_parts = []
        if rule.get("handles"):
            comment_parts.append(f"handles {rule['handles']}")
        if rule.get("on_reject"):
            comment_parts.append(f"on_reject: {rule['on_reject']}")
        header = f"# {fn}" + (f"  ({'; '.join(comment_parts)})" if comment_parts else "")

        cells.append(code_cell(
            f'{header}\n{wrap_comment(rule.get("description", ""))}'
            f'result = apply({call})\n'
            f'dq.record_rule("{fn}", result)\n'
        ))

    # ---- write ----------------------------------------------------------
    quarantine_table = f"{target}{defaults.get('quarantine', {}).get('table_suffix', '_quarantine')}"
    write_mode = defaults.get("write_mode", "overwrite")

    cells.append(code_cell(
        f'# ---- Write -------------------------------------------------------\n'
        f'final_columns = [c["target"] for c in columns]\n'
        f'out = df.select(*[c for c in final_columns if c in df.columns])\n'
        f'\n'
        f'# Audit columns from 00-platform.yaml `audit_columns.silver`.\n'
        f'out = (out\n'
        f'    .withColumn("_processed_at", F.current_timestamp())\n'
        f'    .withColumn("_load_id", F.lit(load_id)))\n'
        f'\n'
        f'rows_out = out.count()\n'
        # overwriteSchema, matching the quarantine write immediately below.
        #
        # Silver is declared fully rebuildable from bronze and writes with
        # mode=overwrite, so its schema is expected to follow the spec. Without
        # this option Delta refuses any write whose schema differs from the
        # existing table, so the FIRST run after a spec adds or removes a
        # column fails -- with a schema-mismatch error inside Spark that Fabric
        # surfaces only as "session failed".
        #
        # The quarantine table beside it already set this; the table everyone
        # actually reads did not, which is why the inconsistency went unnoticed
        # until a column was added.
        + (f'out.write.mode("{write_mode}").option("overwriteSchema", "true") \\\n'
           f'    .format("delta").saveAsTable("{target}")\n'
           if write_mode == "overwrite" else
           f'out.write.mode("{write_mode}").format("delta").saveAsTable("{target}")\n')
        + f'dq.record_output(rows_out)\n'
        f'print(f"wrote {{rows_out:,}} rows to {target}")\n'
        f'\n'
        f'if quarantine is not None:\n'
        f'    rejected_count = quarantine.count()\n'
        f'    # Overwrite, matching silver\'s own write mode. Silver is fully\n'
        f'    # rebuilt each run, so an appended quarantine would accumulate\n'
        f'    # rejects from previous builds and break the layer-level identity\n'
        f'    # count(bronze) == count(silver) + count(quarantine).\n'
        f'    (quarantine.write.mode("overwrite")\n'
        f'        .option("overwriteSchema", "true")\n'
        f'        .format("delta").saveAsTable("{quarantine_table}"))\n'
        f'    dq.record_quarantined(rejected_count)\n'
        f'    print(f"quarantined {{rejected_count:,}} rows to {quarantine_table}")\n'
        f'else:\n'
        f'    rejected_count = 0\n'
    ))

    # ---- reconciliation -------------------------------------------------
    # Some rules MULTIPLY rows on purpose -- explode_json_array turns one
    # persona into one row per building it may see, extend_calendar pads a
    # partial calendar out to whole years. For those the identity
    # count(in) == count(kept) + count(rejected) cannot hold and is not meant
    # to, so asserting it fails a correct build.
    #
    # It failed in exactly that way here: 6 personas exploded to 21 scope rows
    # and the notebook raised "row loss: 6 in, 21 out, 0 quarantined, -15
    # unaccounted" -- naming a row GAIN as a loss, with a negative shortfall as
    # the only clue that the message was describing the opposite of what
    # happened.
    multiplying = ROW_MULTIPLYING_RULES & {r["fn"] for r in table.get("rules", [])}
    if multiplying:
        rule_list = ", ".join(sorted(multiplying))
        cells.append(code_cell(
            f'# ---- Reconciliation ----------------------------------------------\n'
            f'# This table uses a ROW-MULTIPLYING rule ({rule_list}), so\n'
            f'# SILVER-RECON-003 deliberately does not apply: the row count is\n'
            f'# EXPECTED to rise, and a persona scoped to five buildings\n'
            f'# genuinely is five facts.\n'
            f'#\n'
            f'# The invariant that does hold is one-way: nothing may be lost,\n'
            f'# and nothing may be silently dropped instead of quarantined.\n'
            f'# Reconcile the count itself on distinct parent key -- the spec\n'
            f'# declares that check, and D2 records the same fact as\n'
            f'# grain_changes: true.\n'
            f'accounted = rows_out + rejected_count\n'
            f'if accounted < rows_in:\n'
            f'    raise AssertionError(\n'
            f'        f"row loss: {{rows_in:,}} in, {{rows_out:,}} out, "\n'
            f'        f"{{rejected_count:,}} quarantined, "\n'
            f'        f"{{rows_in - accounted:,}} unaccounted -- a multiplying "\n'
            f'        f"rule may add rows but must never lose them"\n'
            f'    )\n'
            f'print(f"row gain by design ({rule_list}): "\n'
            f'      f"{{rows_in:,}} in -> {{rows_out:,}} out, "\n'
            f'      f"{{rejected_count:,}} quarantined")\n'
            f'\n'
            f'dq.flush()\n'
        ))
    else:
        cells.append(code_cell(
            '# ---- Reconciliation ----------------------------------------------\n'
            '# SILVER-RECON-003: every input row is accounted for. A shortfall\n'
            '# means a rule dropped rows without quarantining them, which is a\n'
            '# framework bug rather than a data problem.\n'
            'accounted = rows_out + rejected_count\n'
            'if accounted != rows_in:\n'
            '    raise AssertionError(\n'
            '        f"row loss: {rows_in:,} in, {rows_out:,} out, "\n'
            '        f"{rejected_count:,} quarantined, {rows_in - accounted:,} unaccounted"\n'
            '    )\n'
            'print(f"reconciled: {rows_in:,} = {rows_out:,} kept + {rejected_count:,} quarantined")\n'
            '\n'
            'dq.flush()\n'
        ))

    return notebook(cells, default_lakehouse="lh_silver", known_lakehouses=["lh_bronze", "lh_silver"])


# ---------------------------------------------------------------------------
# Gold notebooks
# ---------------------------------------------------------------------------

def gold_dimension_notebook(platform: dict, dim: dict) -> dict:
    name = dim["name"]
    scd = dim.get("scd", "type1")

    cells = [
        markdown_cell(
            f"# Gold dimension -- `{dim.get('schema', 'dbo')}.{name}`\n\n"
            f"{dim.get('description', '')}\n\n"
            f"**Source:** `{dim.get('source')}`  \n"
            f"**SCD:** `{scd}`  \n"
            f"**Surrogate key:** `{dim.get('surrogate_key')}`  \n"
            f"**Tracked columns:** `{', '.join(dim.get('scd2_tracked_columns', [])) or 'n/a'}`\n\n"
            f"> {BANNER}\n"
        )
    ]
    cells += preamble_cells(platform, "gold", name)

    if dim.get("source") == "generated":
        gen = dim.get("generation", {})
        cells.append(code_cell(
            f'# ---- Generate calendar -------------------------------------------\n'
            f'from ttfabric.dimensions import build_date_dimension\n'
            f'\n'
            f'out = build_date_dimension(\n'
            f'    spark,\n'
            f'    start_date="{gen.get("start_date")}",\n'
            f'    end_date="{gen.get("end_date")}",\n'
            f'    fiscal_year_start_month={gen.get("fiscal_year_start_month", 1)},\n'
            f')\n'
        ))

        # Business rules apply here too.
        #
        # This branch used to return before reaching the shared business_rules
        # loop below, so a rule declared on a generated dimension was accepted
        # by the contract, described in the notebook's own markdown header, and
        # then never emitted. The column simply did not exist -- and because
        # nothing downstream validates a DAX measure against real gold columns,
        # the first sign of it was a semantic model returning blank.
        #
        # A calendar is exactly where derived columns belong: is_working_day
        # and a standard-hours capacity are the denominators most utilization
        # measures divide by.
        for br in dim.get("business_rules", []):
            expression = " ".join(br["expression"].split())
            cells.append(code_cell(
                f'# Business rule: {br["name"]}\n'
                f'# {br.get("description", "").strip()}\n'
                f'out = out.withColumn("{br["target"]}", F.expr("""{expression}"""))\n'
            ))

        cells.append(code_cell(f'gold.write(out, "{name}")\n'))
        return notebook(cells, default_lakehouse="lh_silver", known_lakehouses=["lh_silver"])

    source = dim["source"].replace("silver.", "")
    business_key = dim.get("business_key", [])
    tracked = dim.get("scd2_tracked_columns", [])

    cells.append(code_cell(
        f'# ---- Read silver -------------------------------------------------\n'
        f'src = spark.read.table(f"{{source_item}}.{source}")\n'
        f'print(f"read {{src.count():,}} rows from {source}")\n'
    ))

    # ---- projection ------------------------------------------------------
    # Apply the spec's source -> target renames BEFORE anything references a
    # target name. gold.yaml maps created_at -> customer_since, and the
    # tenure_band rule below reads customer_since; without this the rule fails
    # on a column that silver never had.
    projections = []
    for column in dim.get("columns", []):
        source_column, target = column.get("source"), column["target"]
        if source_column is None:
            continue
        if source_column == target:
            projections.append(f'    F.col("{source_column}"),')
        else:
            projections.append(f'    F.col("{source_column}").alias("{target}"),')

    if projections:
        body = "\n".join(projections)
        cells.append(code_cell(
            f'# ---- Project to the target schema --------------------------------\n'
            f'# Renames come from mappings/gold.yaml `columns`. Applied before the\n'
            f'# business rules, which are written against target names.\n'
            f'src = src.select(\n{body}\n)\n'
        ))

    # Business rules become derived columns.
    for br in dim.get("business_rules", []):
        expression = " ".join(br["expression"].split())
        cells.append(code_cell(
            f'# Business rule: {br["name"]}\n'
            f'# {br.get("description", "").strip()}\n'
            f'src = src.withColumn("{br["target"]}", F.expr("""{expression}"""))\n'
        ))

    if scd == "type2":
        cells.append(code_cell(
            f'# ---- SCD type 2 merge --------------------------------------------\n'
            f'# Opens a new version only when a tracked column changes. An edit\n'
            f'# to an untracked column updates in place, so a phone correction\n'
            f'# does not fabricate a history entry.\n'
            f'from ttfabric.dimensions import merge_scd2\n'
            f'\n'
            f'merge_scd2(\n'
            f'    spark,\n'
            f'    source=src,\n'
            f'    target_table="{name}",\n'
            f'    business_key={business_key!r},\n'
            f'    tracked_columns={tracked!r},\n'
            f'    surrogate_key="{dim.get("surrogate_key")}",\n'
            f'    load_id=load_id,\n'
            f'    gold=gold,\n'
            f')\n'
        ))
    else:
        cells.append(code_cell(
            f'# ---- SCD type 1 overwrite ----------------------------------------\n'
            f'from ttfabric.dimensions import assign_surrogate_key\n'
            f'\n'
            f'out = assign_surrogate_key(src, "{dim.get("surrogate_key")}", {business_key!r})\n'
            f'gold.write(out, "{name}")\n'
        ))

    if dim.get("unknown_member", {}).get("enabled"):
        unknown = dim["unknown_member"]
        cells.append(code_cell(
            f'# ---- Unknown member ----------------------------------------------\n'
            f'# Guarantees an unmatched fact still joins rather than vanishing\n'
            f'# from a report without trace.\n'
            f'from ttfabric.dimensions import ensure_unknown_member\n'
            f'\n'
            f'ensure_unknown_member(\n'
            f'    spark,\n'
            f'    table="{name}",\n'
            f'    surrogate_key="{dim.get("surrogate_key")}",\n'
            f'    key_value={unknown.get("surrogate_key_value", -1)},\n'
            f'    defaults={unknown.get("defaults", {})!r},\n'
            f'    gold=gold,\n'
            f')\n'
            f'dq.flush()\n'
        ))
    else:
        cells.append(code_cell("dq.flush()\n"))

    return notebook(cells, default_lakehouse="lh_silver", known_lakehouses=["lh_silver"])


def gold_fact_notebook(platform: dict, fact: dict, gold: dict) -> dict:
    name = fact["name"]
    source = fact["source"].replace("silver.", "")

    cells = [
        markdown_cell(
            f"# Gold fact -- `{fact.get('schema', 'dbo')}.{name}`\n\n"
            f"{fact.get('description', '')}\n\n"
            f"**Grain:** {' '.join(fact.get('grain', '').split())}\n\n"
            f"> {BANNER}\n"
        )
    ]
    cells += preamble_cells(platform, "gold", name)

    cells.append(code_cell(
        f'# ---- Read silver -------------------------------------------------\n'
        f'df = spark.read.table(f"{{source_item}}.{source}")\n'
    ))

    for join in fact.get("joins", []):
        table = join["table"].replace("silver.", "")
        note = join.get("description", "")
        how = join.get("type", "inner")
        key = join["join_on"]
        policy = join.get("on_unmatched", "drop")

        code = (
            f'# Join {table} ({how})\n'
            f'# {" ".join(note.split())}\n'
            f'{table} = spark.read.table(f"{{source_item}}.{table}")\n'
        )

        if policy == "quarantine" and how == "inner":
            quarantine = join.get("quarantine_table") or f"{fact['name']}_orphans"
            # Remove dbo. prefix if present (Fabric lakehouse uses unqualified names)
            quarantine = quarantine.replace("dbo.", "")
            # Captured BEFORE the join, using left_anti -- the rows the inner
            # join is about to discard. After the join they are simply gone and
            # there is nothing left to count.
            code += (
                f'\n'
                f'# Rows this join discards, captured before it happens.\n'
                f'# on_unmatched: quarantine -- they are a defect, not an\n'
                f'# acceptable loss, so they stay countable and reconcilable\n'
                f'# instead of leaving only a gap in a total.\n'
                f'_orphans = df.join({table}.select("{key}"), on="{key}", how="left_anti")\n'
                f'_orphan_count = _orphans.count()\n'
                f'\n'
                f'# Written ALWAYS, including when empty. Skipping the write on a\n'
                f'# clean run leaves the previous run\'s rows in place, and an\n'
                f'# empty result is exactly when someone trusts what they see --\n'
                f'# so a stale table reads as "these orphans are current".\n'
                f'(_orphans\n'
                f'    .withColumn("_quarantined_at", F.current_timestamp())\n'
                f'    .withColumn("_reason", F.lit("no matching {key} in {table}"))\n'
                f'    .write.mode("overwrite").option("overwriteSchema", "true")\n'
                f'    .saveAsTable("{quarantine}"))\n'
                f'print(f"quarantined {{_orphan_count:,}} row(s) to {quarantine}")\n'
            )

        elif policy == "fail" and how == "inner":
            code += (
                f'\n'
                f'# on_unmatched: fail -- losing a row here is never acceptable.\n'
                f'_unmatched = df.join({table}.select("{key}"), on="{key}",\n'
                f'                     how="left_anti").count()\n'
                f'if _unmatched:\n'
                f'    raise ValueError(\n'
                f'        f"{{_unmatched:,}} row(s) have no matching {key} in "\n'
                f'        f"{table}; on_unmatched is fail")\n'
            )

        code += f'\ndf = df.join({table}, on="{key}", how="{how}")\n'
        cells.append(code_cell(code))

    # The fact's join column and the dimension's business key are different
    # concepts that happen to share a name for customer and product but not for
    # date (order_date_key vs full_date). Resolved from each dimension's own
    # declaration rather than assumed equal.
    dim_business_keys = {
        d["name"]: (d.get("business_key") or [None])[0]
        for d in gold.get("dimensions", [])
    }
    dim_surrogate_keys = {
        d["name"]: d.get("surrogate_key") for d in gold.get("dimensions", [])
    }

    # Business rules FIRST, then the dimension lookups.
    #
    # A dimension key is frequently DERIVED: `opened_date_key` is
    # CAST(opened_at AS date), computed here, and then used as the `lookup_on`
    # for dim_date. Emitting the lookups first meant joining on a column that
    # did not exist yet.
    #
    # The reverse dependency does not occur: a business rule computes a fact
    # attribute from its own source columns, never from a surrogate key it has
    # not been given. The reference project has none, and one would be a
    # different construct anyway -- a rule reading `customer_sk` is asking for
    # a dimension attribute, which belongs in a view.
    #
    # This failed only for facts whose date key is derived in GOLD. A project
    # that derives it in SILVER, as the reference one does, never sees it --
    # which is why the ordering survived.
    for br in fact.get("business_rules", []):
        expression = " ".join(br["expression"].split())
        provisional = br.get("provisional")
        warning = (
            "# PROVISIONAL -- placeholder logic, not real business data.\n"
            "# Replace before this measure informs a decision.\n" if provisional else ""
        )
        cells.append(code_cell(
            f'# Business rule: {br["name"]}\n'
            f'{warning}'
            f'# {" ".join(br.get("description", "").split())}\n'
            f'df = df.withColumn("{br["target"]}", F.expr("""{expression}"""))\n'
        ))

    for key in fact.get("dimension_keys", []):
        as_of = key.get("scd2_as_of")
        cells.append(code_cell(
            f'# Resolve {key["target"]} from {key["dimension"]}\n'
            + (f'# Point-in-time lookup on {as_of}: a fact joins the dimension\n'
               f'# version that was current when the event happened, not the\n'
               f'# version current today.\n' if as_of else '')
            + f'from ttfabric.dimensions import lookup_surrogate_key\n'
              f'\n'
              f'df = lookup_surrogate_key(\n'
              f'    df,\n'
              f'    dimension=gold.read("{key["dimension"]}"),\n'
              f'    surrogate_key="{key["target"]}",\n'
              f'    lookup_on="{key["lookup_on"]}",\n'
              f'    dimension_key="{dim_business_keys.get(key["dimension"], key["lookup_on"])}",\n'
              f'    dimension_surrogate_key="{dim_surrogate_keys.get(key["dimension"], key["target"])}",\n'
            + (f'    as_of_column="{as_of}",\n' if as_of else '')
            + f')\n'
        ))

    # ---- measure renames -------------------------------------------------
    # Applied AFTER the business rules, because those are written against the
    # SOURCE names (subtotal), while the final projection needs the TARGET
    # names (line_revenue). Renaming earlier would break the rules; later, the
    # select would find nothing.
    renames = [(m["source"], m["target"]) for m in fact.get("measures", [])
               if m.get("source") and m["source"] != m["target"]]
    renames += [(d["source"], d["target"]) for d in fact.get("degenerate_dimensions", [])
                if d.get("source") and d["source"] != d["target"]]

    if renames:
        body = "".join(
            f'    .withColumnRenamed("{source}", "{target}")\n' for source, target in renames
        )
        cells.append(code_cell(
            f'# ---- Rename to target names --------------------------------------\n'
            f'df = (df\n{body})\n'
        ))

    measures = [m["target"] for m in fact.get("measures", [])]
    degenerate = [d["target"] for d in fact.get("degenerate_dimensions", [])]
    keys = [k["target"] for k in fact.get("dimension_keys", [])]
    # Keys that must resolve. An `optional: true` key has no value for some
    # rows by design -- an open incident has no resolved date -- so landing on
    # the unknown member is correct there, not a defect. Asserting over it
    # reports an open backlog as a broken lookup.
    required_keys = [k["target"] for k in fact.get("dimension_keys", [])
                     if not k.get("optional")]
    rules = [b["target"] for b in fact.get("business_rules", [])]
    flags = [f["target"] for f in fact.get("quality_flags", [])]

    # Deduplicated, preserving order.
    #
    # A measure whose `target` matches a business rule's `target` -- which is
    # the natural way to write "this rule computes this measure" -- listed the
    # column twice. `df.select` then produces two columns with the same name,
    # and the Delta write fails on the duplicate. The failure surfaces as a
    # Spark statement error with no mention of the column, so it reads as a
    # data problem rather than a spec one.
    final: list[str] = []
    for column in degenerate + keys + measures + rules + flags:
        if column not in final:
            final.append(column)

    cells.append(code_cell(
        f'# ---- Write -------------------------------------------------------\n'
        f'final_columns = {final!r}\n'
        f'out = df.select(*[c for c in final_columns if c in df.columns])\n'
        f'# Drop existing audit columns before re-adding (silver layer may have them)\n'
        f'for col in ["_built_at", "_load_id", "_processed_at"]:\n'
        f'    if col in out.columns:\n'
        f'        out = out.drop(col)\n'
        f'# Add gold audit columns\n'
        f'out = (out\n'
        f'    .withColumn("_built_at", F.current_timestamp())\n'
        f'    .withColumn("_load_id", F.lit(load_id)))\n'
        f'\n'
        f'gold.write(out, "{name}")\n'
        f'print(f"wrote {{out.count():,}} rows to {name}")\n'
    ))

    tests = fact.get("tests", [])
    if tests:
        assertions = "".join(
            f'#   {t["id"]}: {" ".join(t["description"].split())}\n' for t in tests
        )
        # The grain is whatever the spec DECLARES unique -- and only that.
        grain_test = next(
            (t for t in tests if t.get("assertion", "").startswith("unique(")), None
        )
        if grain_test:
            # Split on commas: `unique(user_sk, week_start_date_sk)` is a
            # COMPOSITE grain. Taking the whole string as one name produced a
            # column literally called "user_sk, week_start_date_sk", which
            # resolves against nothing.
            inner = grain_test["assertion"][len("unique("):].rstrip(")")
            grain_columns = [c.strip() for c in inner.split(",") if c.strip()]
        else:
            # No uniqueness assertion is emitted when the spec declares none.
            #
            # This used to fall back to "assert the LAST degenerate dimension
            # is unique", which is a guess, not a grain. A degenerate dimension
            # is an attribute carried on the fact -- a priority, a stage, a free
            # text message -- and there is no reason for it to be unique.
            #
            # It failed exactly as you would expect: fct_outage asserted
            # `outage_message` unique across 427 outages and fct_ticket_sla
            # asserted `sla_stage`. Both raise inside Spark, which Fabric
            # reports only as "session failed", so a fact with a perfectly good
            # grain looked like a data problem.
            grain_columns = []

        # Reconciliation back to the source measure. Declared in the spec as
        # GOLD-RECON-001; previously only a comment, which meant the one check
        # that proves the warehouse agrees with its source never ran.
        source_table = fact["source"].replace("silver.", "")
        # Reconcile on MONEY, not on whatever additive measure comes first.
        # quantity and line_revenue are both additive sums, and a check that
        # ties out unit counts while revenue drifts is worse than no check --
        # it reports assurance it has not earned.
        additive = [m for m in fact.get("measures", [])
                    if m.get("aggregation") == "sum" and m.get("additive")]
        monetary = [m for m in additive if m.get("type") == "decimal"]
        chosen = (monetary or additive)
        source_measure = chosen[0]["source"] if chosen else None
        target_measure = chosen[0]["target"] if chosen else None

        recon = ""
        if source_measure and target_measure:
            recon = (
                f'\n'
                f'# Gold must tie back to silver. Compared against the rows that\n'
                f'# actually reached gold -- the inner join legitimately drops\n'
                f'# lines whose header was quarantined, so comparing against all\n'
                f'# of silver would fail for a correct build.\n'
                f'reachable = (spark.read.table(f"{{source_item}}.{source_table}")\n'
                f'    .join(spark.read.table(f"{{source_item}}.{fact["joins"][0]["table"].replace("silver.", "")}")\n'
                f'          .select("{fact["joins"][0]["join_on"]}"),\n'
                f'          on="{fact["joins"][0]["join_on"]}", how="inner"))\n'
                f'expected = reachable.agg(F.sum("{source_measure}")).collect()[0][0] or 0\n'
                f'actual = out.agg(F.sum("{target_measure}")).collect()[0][0] or 0\n'
                f'assert_reconciles(float(actual), float(expected), 0.01,\n'
                f'                  "{fact["name"]}.{target_measure} vs {source_table}.{source_measure}")\n'
            ) if fact.get("joins") else ""

        cells.append(code_cell(
            f'# ---- Tests -------------------------------------------------------\n'
            f'{assertions}'
            f'from ttfabric.quality import (assert_unique, assert_not_null,\n'
            f'                         assert_keys_resolve, assert_reconciles)\n'
            f'\n'
            + (f'assert_unique(out, {grain_columns!r})\n' if grain_columns else
               f'# No uniqueness assertion: the spec declares no unique() test\n'
               f'# for this fact. Guessing one is worse than omitting it.\n')
            + f'assert_not_null(out, {required_keys!r})\n'
            f'\n'
            f'# not_null is not enough: a failed lookup yields the unknown-member\n'
            f'# key, not a null, so a fact table with every key unresolved passes\n'
            f'# a not-null check while reporting everything against "Unknown".\n'
            f'#\n'
            f'# Optional keys are excluded: an event that has not happened has\n'
            f'# no date, and the unknown member is the correct destination.\n'
            f'assert_keys_resolve(out, {required_keys!r})\n'
            f'{recon}'
            f'\n'
            f'dq.record_input(out.count())\n'
            f'dq.record_output(out.count())\n'
            f'dq.flush()\n'
        ))

    return notebook(cells, default_lakehouse="lh_silver", known_lakehouses=["lh_silver"])


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def bi_views_notebook(platform: dict, gold: dict) -> dict:
    """Create the `bi` reporting views, after the tables they read exist.

    The views select from dbo.dim_* and dbo.fct_*, and those tables are created
    by SPARK through the warehouse connector when the gold notebooks run -- not
    by DDL, deliberately, so that nothing races the connector over the same
    definition.

    That makes the views un-creatable at deploy time on a fresh environment.
    Running the migration before the first load produced
    `Invalid object name 'dbo.fct_sales'` on all four, and only the DQ gate
    noticed: every deploy step reported success, and the semantic model binds to
    dbo, so nothing looked wrong until someone opened a report.

    So the views are built HERE, as the last step of the gold pipeline, once
    every dimension and fact has been written. CREATE OR ALTER means a re-run
    converges rather than failing.

    T-SQL over JDBC: the Spark connector reads and writes tables but issues no
    DDL, and the REST API manages items rather than their contents.
    """
    reporting = gold["defaults"].get("reporting_schema", "bi")
    physical = gold["defaults"].get("physical_schema", "dbo")
    warehouse = ((platform.get("storage") or {}).get("items") or {}).get(
        "medallion", {}).get("gold", {}).get("name", "wh_gold")

    statements = []
    for view in gold.get("views", []):
        schema = view.get("schema", reporting)
        body = view["sql"].rstrip().rstrip(";")
        statements.append(
            f"CREATE OR ALTER VIEW [{schema}].[{view['name']}] AS\n{body}")

    cells = [
        markdown_cell(
            "# Build the reporting views\n\n"
            "Generated from `fabric/05-gold.yaml` — do not edit by hand.\n\n"
            "Runs **after every dimension and fact**, because these views "
            "select from tables that Spark creates through the warehouse "
            "connector. They cannot exist before the first load, which is why "
            "they are built here rather than by a deploy-time migration.\n\n"
            "`CREATE OR ALTER`, so a re-run converges instead of failing."
        ),
        code_cell(
            f"WAREHOUSE = {warehouse!r}\n"
            f"REPORTING_SCHEMA = {reporting!r}\n"
            f"PHYSICAL_SCHEMA = {physical!r}\n"
            "\n"
            f"VIEWS = {pprint.pformat([v['name'] for v in gold.get('views', [])], width=80)}\n"
            f"STATEMENTS = {pprint.pformat(statements, width=100)}\n"
        ),
        code_cell(
            "import com.microsoft.spark.fabric  # noqa: F401  registers the connector\n"
            "\n"
            "# The warehouse SQL endpoint, resolved from the item itself rather\n"
            "# than hard-coded, so this notebook promotes unchanged.\n"
            "endpoint = (spark.conf.get('trident.workspace.id'), WAREHOUSE)\n"
            "import sempy.fabric as fabric_api\n"
            "wh = [w for w in fabric_api.list_items('Warehouse').itertuples()\n"
            "      if w[fabric_api.list_items('Warehouse').columns.get_loc('Display Name') + 1]\n"
            "      == WAREHOUSE]\n"
        ),
    ]

    # sempy is not guaranteed present; resolve the endpoint the way every other
    # warehouse-touching notebook does instead.
    cells[-1] = code_cell(
        "import com.microsoft.spark.fabric  # noqa: F401  registers the connector\n"
        "import requests\n"
        "\n"
        "workspace_id = spark.conf.get('trident.workspace.id')\n"
        "token = mssparkutils.credentials.getToken('https://api.fabric.microsoft.com')\n"
        "\n"
        "# Resolved at RUN time from the workspace this notebook is in, so the\n"
        "# same artefact points at dev's warehouse in dev and qa's in qa.\n"
        "warehouses = requests.get(\n"
        "    f'https://api.fabric.microsoft.com/v1/workspaces/{workspace_id}/warehouses',\n"
        "    headers={'Authorization': f'Bearer {token}'}, timeout=60).json()['value']\n"
        "target = next(w for w in warehouses if w['displayName'] == WAREHOUSE)\n"
        "endpoint = target['properties']['connectionString']\n"
        "print(f'{WAREHOUSE} -> {endpoint}')\n"
    )

    cells.append(code_cell(
        "sql_token = mssparkutils.credentials.getToken('https://database.windows.net/')\n"
        "jvm = spark._jvm\n"
        "props = jvm.java.util.Properties()\n"
        "props.setProperty('accessToken', sql_token)\n"
        "props.setProperty('encrypt', 'true')\n"
        "conn = jvm.java.sql.DriverManager.getConnection(\n"
        "    f'jdbc:sqlserver://{endpoint}:1433;database={WAREHOUSE}', props)\n"
        "conn.setAutoCommit(True)\n"
        "stmt = conn.createStatement()\n"
        "\n"
        "# Each view is applied independently. One malformed view must not stop\n"
        "# the other three -- and a single aggregate error hides how many were\n"
        "# actually fine.\n"
        "failed = []\n"
        "for name, sql in zip(VIEWS, STATEMENTS):\n"
        "    try:\n"
        "        stmt.execute(sql)\n"
        "        print(f'  ok      {REPORTING_SCHEMA}.{name}')\n"
        "    except Exception as exc:\n"
        "        failed.append(name)\n"
        "        print(f'  FAILED  {REPORTING_SCHEMA}.{name}: {str(exc)[:160]}')\n"
        "\n"
        "stmt.close(); conn.close()\n"
        "\n"
        "if failed:\n"
        "    raise RuntimeError(\n"
        "        f'{len(failed)} of {len(VIEWS)} view(s) could not be created: '\n"
        "        + ', '.join(failed))\n"
        "print(f'\\nall {len(VIEWS)} reporting view(s) are current')\n"
    ))

    silver_item = ((platform.get("storage") or {}).get("items") or {}).get(
        "medallion", {}).get("silver", {}).get("name", "lh_silver")
    return notebook(cells, default_lakehouse=silver_item)


def cascade_notebook(platform: dict, silver: dict) -> dict:
    """Propagate parent rejections to their children, after the whole layer.

    A separate notebook rather than a cell on either table, because it can
    belong to neither: the parent is cleansed after the child wherever a header
    is corrected from its lines, so at the time the child is written it is not
    yet known which parents survive.
    """
    cascades = silver["cascade_quarantine"]
    suffix = ((silver.get("defaults") or {}).get("quarantine") or {}).get(
        "table_suffix", "_quarantine")
    storage = platform.get("storage") or {}
    silver_item = ((storage.get("items") or {}).get("medallion") or {}).get(
        "silver", {}).get("name", "lh_silver")

    cells = [
        markdown_cell(
            "# Cascade quarantine\n\n"
            "Generated from `fabric/04-silver.yaml` — do not edit by hand.\n\n"
            "A row rejected at one table orphans its children at every table "
            "below it, and nothing notices: the children are valid in "
            "isolation, so no rule fires and they travel on until a join in a "
            "later layer discards them silently.\n\n"
            "This runs **after** every silver table is built. It cannot be "
            "part of a child's own build — a parent is often cleansed after "
            "its child, because an order header is corrected from its lines."
        ),
        code_cell(
            "from datetime import datetime, timezone\n"
            "\n"
            "from ttfabric.cleansing import cascade_quarantine\n"
            "\n"
            f"CASCADES = {pprint.pformat(cascades, width=84, sort_dicts=False)}\n"
            f"QUARANTINE_SUFFIX = {suffix!r}\n"
            "\n"
            "load_id = f\"load_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}\"\n"
            "print(f\"cascade run {load_id}\")\n"
        ),
        code_cell(
            "total = 0\n"
            "for rule in CASCADES:\n"
            "    result = cascade_quarantine(\n"
            "        spark,\n"
            "        child=rule['child'],\n"
            "        parent=rule['parent'],\n"
            "        join_on=rule['join_on'],\n"
            "        reason=rule['reason'],\n"
            "        load_id=load_id,\n"
            "        quarantine_suffix=QUARANTINE_SUFFIX,\n"
            "    )\n"
            "    total += result['quarantined']\n"
            "    print(f\"  {result['child']:<20} <- {result['parent']:<14} \"\n"
            "          f\"{result['quarantined']:>7,} row(s) quarantined\")\n"
            "\n"
            "# Zero is the healthy state once upstream is clean. It is reported\n"
            "# rather than asserted: the rows are a defect to fix, not a reason\n"
            "# to stop the layer that correctly identified them.\n"
            "print(f\"\\n{total:,} row(s) cascaded in total\")\n"
        ),
    ]

    return notebook(cells, default_lakehouse=silver_item)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--specs", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--check", action="store_true",
                        help="report drift without writing")
    args = parser.parse_args()

    spec_dir = Path(args.specs)
    out_dir = Path(args.out)

    # Resolved through _specs so either layout works.
    platform = load_spec(spec_dir, "scaffolding")
    sources = load_spec(spec_dir, "sources")
    bronze = load_spec(spec_dir, "bronze")
    silver = load_spec(spec_dir, "silver")
    gold = load_spec(spec_dir, "gold")

    template = platform["naming"]["notebook"]
    # A verb per layer, so an item name says what it DOES. The layer itself is
    # already carried by the storage item and the workspace folder.
    verbs = platform["naming"]["verbs"]
    planned: dict[Path, str] = {}

    def name_for(layer: str, entity: str) -> str:
        return template.format(verb=verbs[layer], layer=layer, entity=entity) + ".ipynb"

    source_by_name = {s["name"]: s for s in sources["sources"]}
    for mapping in bronze.get("tables", []):
        source_name, entity_name = mapping["source_entity"].split(".", 1)
        source = source_by_name[source_name]
        entity = next(e for e in source["entities"] if e["name"] == entity_name)
        # Named from the SOURCE entity, not the bronze table -- the table
        # already carries a `bronze_` prefix and would read nb_load_bronze_...
        planned[out_dir / name_for("bronze", entity_name)] = render(
            bronze_notebook(platform, mapping, entity, source, bronze.get("defaults", {}))
        )

    for table in silver.get("tables", []):
        planned[out_dir / name_for("silver", table["target"])] = render(
            silver_notebook(platform, table, silver.get("defaults", {}))
        )

    if silver.get("cascade_quarantine"):
        planned[out_dir / "nb_cascade_quarantine.ipynb"] = render(
            cascade_notebook(platform, silver)
        )

    for dim in gold.get("dimensions", []):
        planned[out_dir / name_for("gold", dim["name"])] = render(
            gold_dimension_notebook(platform, dim)
        )

    for fact in gold.get("facts", []):
        planned[out_dir / name_for("gold", fact["name"])] = render(
            gold_fact_notebook(platform, fact, gold)
        )

    # Named nb_build_bi_views so it matches the gold verb and the nb_build_*
    # folder pattern, and is filed with the rest of the layer automatically.
    if gold.get("views"):
        planned[out_dir / "nb_build_bi_views.ipynb"] = render(
            bi_views_notebook(platform, gold)
        )

    if args.check:
        drifted = []
        for path, content in planned.items():
            if not path.exists():
                drifted.append(f"{path.name} (missing)")
            elif path.read_text(encoding="utf-8") != content:
                drifted.append(f"{path.name} (differs from spec)")
        if drifted:
            print("Generated notebooks are out of date with the specs:")
            for item in drifted:
                print(f"  {item}")
            print("\nRun generate_notebooks.py without --check to regenerate.")
            return 1
        print(f"All {len(planned)} notebooks match the specs.")
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)
    for path, content in planned.items():
        path.write_text(content, encoding="utf-8")

    print(f"Generated {len(planned)} notebooks in {out_dir}")
    for path in sorted(planned):
        print(f"  {path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
