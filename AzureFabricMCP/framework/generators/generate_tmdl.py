"""
Generate a Power BI semantic model (TMDL) from the P1 spec.

TMDL is the text format Fabric stores a semantic model in: one file per table
plus a model, relationships and expressions file. Generating it -- rather than
building the model in Power BI Desktop -- keeps the model reviewable in a pull
request and identical across environments.

Lineage tags are deterministic
------------------------------
Every TMDL object carries a lineageTag GUID that reports bind to. A random GUID
per generation would mean each deploy silently rebuilt the model and detached
every report from it. Here the tags are UUID5 values derived from the model name
and the object path, so regenerating produces byte-identical output and a report
keeps its bindings.

Usage:
    python generate_tmdl.py --specs ./01_demo-project --out ./01_demo-project/generated/model
    python generate_tmdl.py --specs ./01_demo-project --out ... --check
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import shutil
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _specs import load as load_spec

# Stable namespace. Changing it re-tags every object in every model, which
# detaches existing reports -- so it is a constant, never derived.
NAMESPACE = uuid.UUID("6f9619ff-8b86-d011-b42d-00c04fc964ff")

# Source type -> tabular type. Covers both the logical types the gold spec
# declares and the warehouse types INFORMATION_SCHEMA reports, so types can come
# from either without a second mapping table.
#
# An unmapped type becomes a string, which loses no data but cannot be
# aggregated -- so a revenue column typed as string produces a model where SUM
# is unavailable rather than one that errors. Unmapped types are reported.
DATA_TYPES = {
    # logical (gold spec)
    "string": "string", "integer": "int64", "long": "int64", "short": "int64",
    "double": "double", "float": "double", "timestamp": "dateTime",
    "boolean": "boolean", "date": "dateTime", "decimal": "decimal",
    # warehouse (INFORMATION_SCHEMA)
    "varchar": "string", "nvarchar": "string", "char": "string", "nchar": "string",
    "int": "int64", "bigint": "int64", "smallint": "int64", "tinyint": "int64",
    "numeric": "decimal", "money": "decimal", "real": "double",
    "datetime": "dateTime", "datetime2": "dateTime", "bit": "boolean",
}

# Columns the framework adds rather than the mapping declaring them: surrogate
# keys, SCD2 validity, and load metadata. Their types are a property of the
# framework, so they are known here rather than restated in every project spec.
FRAMEWORK_COLUMNS = {
    "valid_from": "date", "valid_to": "date", "is_current": "boolean",
    "version": "integer", "_load_id": "string", "_tracked_hash": "string",
    "_built_at": "timestamp",
}

# DirectLake reads delta files. A view has none, so a model bound to one falls
# back to DirectQuery without reporting anything.
PHYSICAL_ONLY_MODES = {"direct_lake"}

MODE = {"direct_lake": "directLake", "import": "import", "direct_query": "directQuery"}


def tag(model: str, *path: str) -> str:
    return str(uuid.uuid5(NAMESPACE, f"{model}/{'/'.join(path)}"))


def types_from_gold(gold: dict) -> dict[str, str]:
    """Map 'schema.table.column' -> tabular type using the gold mapping.

    The gold spec is the authoritative description of what the warehouse holds,
    so types come from there rather than from a live query -- generation stays
    offline and a model can be built before the warehouse exists.
    """
    schema = gold.get("defaults", {}).get("physical_schema", "dbo")
    resolved: dict[str, str] = {}

    for entity in list(gold.get("dimensions", [])) + list(gold.get("facts", [])):
        table = f"{entity.get('schema', schema)}.{entity['name']}".lower()

        # A typed column can be declared in any of these. Dimensions use
        # `columns`; facts split the same idea across measures, degenerate
        # dimensions and derived business rules. Reading only one of them
        # types the rest as string -- which for a revenue column means the
        # model simply has no SUM, with nothing to indicate why.
        for section in ("columns", "measures", "degenerate_dimensions",
                        "business_rules", "quality_flags"):
            for column in entity.get(section) or []:
                target = column.get("target") or column.get("source") or column.get("name")
                if not target:
                    continue
                declared = (column.get("type") or "string").split("(")[0].lower()
                resolved[f"{table}.{target}"] = DATA_TYPES.get(declared, "string")

        # Foreign keys to dimensions are surrogate keys: hashes, so 64-bit.
        for key in entity.get("dimension_keys") or []:
            target = key.get("target") or key.get("name")
            if target:
                resolved[f"{table}.{target}"] = "int64"

        # Surrogate key: a hash, so 64-bit regardless of the business key type.
        if entity.get("surrogate_key"):
            resolved[f"{table}.{entity['surrogate_key']}"] = "int64"

        for name, declared in FRAMEWORK_COLUMNS.items():
            resolved.setdefault(f"{table}.{name}", DATA_TYPES[declared])

    return resolved


def quote(name: str) -> str:
    """TMDL quotes an identifier only when it is not a bare word."""
    if name and all(c.isalnum() or c == "_" for c in name) and not name[0].isdigit():
        return name
    return f"'{name}'"


def indent(text: str, level: int) -> str:
    pad = "\t" * level
    return "\n".join(pad + line if line.strip() else line
                     for line in text.splitlines())


def column_tmdl(model: str, table: dict, column: dict, types: dict,
                hide_patterns: list[str], resolver: dict) -> str:
    source = column["source"]
    name = column.get("name", source)
    key = f"{table['source_table']}.{source}".lower()
    data_type = types.get(key, "string")

    hidden = column.get("hidden")
    if hidden is None:
        hidden = any(fnmatch.fnmatch(source, p) for p in hide_patterns)

    lines = [f"column {quote(name)}",
             f"\tdataType: {data_type}"]
    if hidden:
        lines.append("\tisHidden")
    lines.append(f"\tsourceColumn: {source}")
    lines.append(f"\tsummarizeBy: {column.get('summarize_by', 'none')}")
    if column.get("format"):
        lines.append(f"\tformatString: {column['format']}")
    if column.get("sort_by"):
        # Resolved to the display name: a sort_by naming the source column
        # points at nothing, and the model loads with the sort silently absent.
        sort_by = resolver.get((table["name"], column["sort_by"]), column["sort_by"])
        lines.append(f"\tsortByColumn: {quote(sort_by)}")
    if column.get("data_category"):
        lines.append(f"\tdataCategory: {column['data_category']}")
    if column.get("description"):
        # TMDL descriptions are comment lines above the object.
        note = " ".join(column["description"].split())
        lines.insert(0, f"/// {note}")
    lines.append(f"\tlineageTag: {tag(model, 'table', table['name'], 'column', name)}")
    return "\n".join(lines)


def measure_tmdl(model: str, measure: dict, default_format: str | None) -> str:
    lines = []
    if measure.get("description"):
        lines.append(f"/// {' '.join(measure['description'].split())}")

    expression = measure["expression"].strip()
    if "\n" in expression:
        lines.append(f"measure {quote(measure['name'])} =")
        lines.append(indent(expression, 2))
    else:
        lines.append(f"measure {quote(measure['name'])} = {expression}")

    fmt = measure.get("format") or default_format
    if fmt:
        lines.append(f"\tformatString: {fmt}")
    if measure.get("hidden"):
        lines.append("\tisHidden")
    if measure.get("display_folder"):
        lines.append(f"\tdisplayFolder: {measure['display_folder']}")
    lines.append(f"\tlineageTag: {tag(model, 'measure', measure['name'])}")
    return "\n".join(lines)


def table_tmdl(spec: dict, table: dict, types: dict, resolver: dict) -> str:
    model = spec["model"]["name"]
    mode = MODE[spec["model"]["storage_mode"]]
    hide_patterns = (spec.get("defaults", {}).get("hide_columns_matching")
                     or ["_*", "*_sk", "_tracked_hash"])
    default_format = spec.get("defaults", {}).get("measure_format")

    blocks = []

    # The description must be the line directly above the object it describes.
    # Emitting it as its own block puts a blank line between the two, and TMDL
    # rejects the file outright: "Unexpected line type: Empty!".
    header = []
    if table.get("description"):
        header.append(f"/// {' '.join(table['description'].split())}")
    header.append(f"table {quote(table['name'])}")
    if table.get("hidden"):
        header.append("\tisHidden")
    header.append(f"\tlineageTag: {tag(model, 'table', table['name'])}")

    # MarkAsDateTable. Without it, time intelligence returns wrong answers
    # rather than refusing to run.
    if table["kind"] == "date":
        date_column = next((c.get("name", c["source"]) for c in table.get("columns", [])
                            if c["source"] in ("full_date", "date")), None)
        if date_column:
            header.append("\tdataCategory: Time")
    blocks.append("\n".join(header))

    for column in table.get("columns", []):
        body = column_tmdl(model, table, column, types, hide_patterns, resolver)
        # The date table's key column must be marked, not merely present.
        if table["kind"] == "date" and column["source"] in ("full_date", "date"):
            body += "\n\tisKey"
        blocks.append(indent(body, 1))

    for measure in spec.get("measures", []):
        if measure["table"] == table["name"]:
            blocks.append(indent(measure_tmdl(model, measure, default_format), 1))

    for hierarchy in spec.get("hierarchies", []):
        if hierarchy["table"] != table["name"]:
            continue
        lines = [f"hierarchy {quote(hierarchy['name'])}",
                 f"\tlineageTag: {tag(model, 'hierarchy', hierarchy['name'])}"]
        for level_index, level in enumerate(hierarchy["levels"]):
            lines.append(f"\n\tlevel {quote(level)}")
            lines.append(f"\t\tlineageTag: {tag(model, 'hierarchy', hierarchy['name'], level)}")
            lines.append(f"\t\tcolumn: {quote(level)}")
        blocks.append(indent("\n".join(lines), 1))

    schema, entity = table["source_table"].split(".", 1)
    partition = [
        f"partition {quote(table['name'])} = entity",
        f"\tmode: {mode}",
        "\tsource",
        f"\t\tentityName: {entity}",
        f"\t\tschemaName: {schema}",
        "\t\texpressionSource: DatabaseQuery",
    ]
    blocks.append(indent("\n".join(partition), 1))

    return "\n\n".join(blocks) + "\n"


def prepare(spec: dict) -> dict[tuple[str, str], str]:
    """Add the key columns relationships need, and map references to display names.

    A spec declares business columns; surrogate keys are plumbing, so requiring
    an author to list `customer_sk` purely to make a relationship resolve is
    noise that will eventually be forgotten. They are injected here as hidden
    columns instead.

    Returns {(table, reference): display name}. A relationship may name either
    the source column or the display name, and TMDL needs the display name --
    referring to the source name produces "refers to an object which cannot be
    found", naming the relationship rather than the column.
    """
    needed: dict[str, set[str]] = {}
    for rel in spec["relationships"]:
        needed.setdefault(rel["from_table"], set()).add(rel["from_column"])
        needed.setdefault(rel["to_table"], set()).add(rel["to_column"])

    resolver: dict[tuple[str, str], str] = {}
    for table in spec["tables"]:
        wanted = needed.get(table["name"], set())
        if table.get("key"):
            wanted = wanted | {table["key"]}

        columns = table.setdefault("columns", [])
        known = {c["source"] for c in columns} | {c.get("name", c["source"]) for c in columns}
        for column in sorted(wanted - known):
            columns.append({"source": column, "hidden": True})

        for column in columns:
            display = column.get("name", column["source"])
            resolver[(table["name"], column["source"])] = display
            resolver[(table["name"], display)] = display

    return resolver


def build(spec: dict, types: dict) -> dict[str, str]:
    model_name = spec["model"]["name"]
    resolver = prepare(spec)
    files: dict[str, str] = {}

    for table in spec["tables"]:
        files[f"definition/tables/{table['name']}.tmdl"] = table_tmdl(
            spec, table, types, resolver)

    # --- relationships ------------------------------------------------------
    CARDINALITY = {"many_to_one": ("*", "1"), "one_to_many": ("1", "*"),
                   "one_to_one": ("1", "1"), "many_to_many": ("*", "*")}
    rels = []
    for rel in spec["relationships"]:
        from_card, to_card = CARDINALITY[rel.get("cardinality", "many_to_one")]
        name = tag(model_name, "rel", rel["from_table"], rel["from_column"],
                   rel["to_table"], rel["to_column"])
        # `description` on a relationship is documentation only and is NOT
        # emitted. A relationship has no Description property in the tabular
        # model ("///" fails the file), and a plain "//" comment is rejected
        # for indentation in this position. The spec keeps the explanation,
        # which is where a reviewer reads it anyway.
        lines = [f"relationship {name}"]
        if not rel.get("active", True):
            lines.append("\tisActive: false")
        if rel.get("cross_filter") == "both":
            lines.append("\tcrossFilteringBehavior: bothDirections")
        if from_card != "*":
            lines.append(f"\tfromCardinality: {'one' if from_card == '1' else 'many'}")
        if to_card != "1":
            lines.append(f"\ttoCardinality: {'one' if to_card == '1' else 'many'}")
        from_column = resolver[(rel["from_table"], rel["from_column"])]
        to_column = resolver[(rel["to_table"], rel["to_column"])]
        lines.append(f"\tfromColumn: {quote(rel['from_table'])}.{quote(from_column)}")
        lines.append(f"\ttoColumn: {quote(rel['to_table'])}.{quote(to_column)}")
        rels.append("\n".join(lines))
    files["definition/relationships.tmdl"] = "\n\n".join(rels) + "\n"

    # --- roles --------------------------------------------------------------
    roles = []
    for role in spec.get("roles", []):
        lines = []
        if role.get("description"):
            lines.append(f"/// {' '.join(role['description'].split())}")
        lines.append(f"role {quote(role['name'])}")
        lines.append("\tmodelPermission: read")
        for filt in role["filters"]:
            lines.append("")
            lines.append(f"\ttablePermission {quote(filt['table'])} = {filt['expression']}")
        roles.append("\n".join(lines))
    if roles:
        files["definition/roles.tmdl"] = "\n\n".join(roles) + "\n"

    # --- the shared source expression --------------------------------------
    # Every partition points at this one expression, so the endpoint is
    # declared once and rewritten once per environment at deploy time.
    source = spec["model"]["source"]
    files["definition/expressions.tmdl"] = (
        "/// Connection to the gold layer. The endpoint is environment-specific\n"
        "/// and is substituted at deploy time by push_semantic_model.py.\n"
        "expression DatabaseQuery =\n"
        "\t\tlet\n"
        '\t\t    database = Sql.Database("@@sqlendpoint@@", "@@database@@")\n'
        "\t\tin\n"
        "\t\t    database\n"
        f"\tlineageTag: {tag(model_name, 'expression', 'DatabaseQuery')}\n"
        "\tannotation PBI_IncludeFutureArtifacts = False\n"
        f"\tannotation PBI_NavigationStepName = Navigation\n"
    )

    # --- model --------------------------------------------------------------
    culture = spec["model"].get("culture", "en-US")
    model_lines = []
    if spec["model"].get("description"):
        model_lines.append(f"/// {' '.join(spec['model']['description'].split())}")
    model_lines += [
        "model Model",
        f"\tculture: {culture}",
        "\tdefaultPowerBIDataSourceVersion: powerBI_V3",
        # discourageImplicitMeasures is what stops a report author dragging a
        # raw column onto a visual and inventing an aggregate the model does
        # not define.
        "\tdiscourageImplicitMeasures",
        "\tsourceQueryCulture: " + culture,
        "",
        "annotation PBI_QueryOrder = " + json.dumps(["DatabaseQuery"]),
        "",
        "ref table " + "\nref table ".join(quote(t["name"]) for t in spec["tables"]),
        "",
        "ref expression DatabaseQuery",
        "",
        "ref relationship " + "\nref relationship ".join(
            tag(model_name, "rel", r["from_table"], r["from_column"],
                r["to_table"], r["to_column"]) for r in spec["relationships"]),
    ]
    if roles:
        model_lines += ["", "ref role " + "\nref role ".join(
            quote(r["name"]) for r in spec.get("roles", []))]
    files["definition/model.tmdl"] = "\n".join(model_lines) + "\n"

    files["definition/database.tmdl"] = (
        "database\n"
        "\tcompatibilityLevel: 1604\n"
    )

    files["definition.pbism"] = json.dumps({
        "version": "4.2",
        "settings": {},
    }, indent=2) + "\n"

    files[".platform"] = json.dumps({
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/"
                   "gitIntegration/platformProperties/2.0.0/schema.json",
        "metadata": {"type": "SemanticModel", "displayName": model_name,
                     "description": " ".join(
                         spec["model"].get("description", "").split())[:250]},
        "config": {"version": "2.0", "logicalId": tag(model_name, "item")},
    }, indent=2) + "\n"

    return files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--specs", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--types", help="JSON map of 'schema.table.column' -> warehouse type")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    spec = load_spec(Path(args.specs), "semantic-model", track="powerbi")

    mode = spec["model"]["storage_mode"]
    if mode in PHYSICAL_ONLY_MODES:
        views = [t["source_table"] for t in spec["tables"]
                 if t["source_table"].split(".")[0] != "dbo"]
        if views:
            print(f"  ERROR  storage_mode is {mode}, which reads delta files, but "
                  f"these bind to a non-physical schema:")
            for view in views:
                print(f"           {view}")
            print("         DirectLake cannot read a view. It will not fail -- it will "
                  "fall back to\n         DirectQuery and be slower for no visible reason.")
            return 1

    if args.types:
        types = {k.lower(): DATA_TYPES.get(v.split("(")[0].lower(), "string")
                 for k, v in json.loads(Path(args.types).read_text()).items()}
    else:
        types = types_from_gold(load_spec(Path(args.specs), "gold"))

    # A column the gold mapping does not describe would silently become a
    # string -- and a revenue column typed as string is a model where SUM is
    # simply missing, with nothing to explain why.
    unmapped = [f"{t['source_table']}.{c['source']}"
                for t in spec["tables"] for c in t.get("columns", [])
                if f"{t['source_table']}.{c['source']}".lower() not in types]
    if unmapped:
        print("  WARN   not described by the gold mapping; defaulting to string:")
        for name in unmapped:
            print(f"           {name}")

    out = Path(args.out)
    planned = build(spec, types)

    if args.check:
        drifted = [name for name, text in planned.items()
                   if not (out / name).exists()
                   or (out / name).read_text(encoding="utf-8") != text]
        if drifted:
            print("Generated model is out of date with the spec:")
            for name in drifted:
                print(f"  {name}")
            return 1
        print(f"All {len(planned)} model files match the spec.")
        return 0

    if out.exists():
        # A renamed or removed table would otherwise linger as an orphan file
        # and be deployed as part of the model.
        shutil.rmtree(out)
    for name, text in planned.items():
        path = out / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    tables = len(spec["tables"])
    measures = len(spec.get("measures", []))
    print(f"Generated {spec['model']['name']} in {out}")
    print(f"  {tables} tables, {len(spec['relationships'])} relationships, "
          f"{measures} measures, {len(spec.get('hierarchies', []))} hierarchies")
    print(f"  storage mode: {mode}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
