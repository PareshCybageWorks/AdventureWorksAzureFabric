"""
Generate Power BI reports (PBIR) from the P2 spec.

PBIR is the folder format Fabric stores a report in: a page per folder, a visual
per folder inside it. Generating it keeps a report reviewable as a diff -- a
moved visual is two changed numbers, not an opaque binary.

Field references
----------------
The spec names fields as "Table.Field" using display names. Whether a reference
is a Column or a Measure is NOT declared: it is resolved from the P1 semantic
model, because that is where the answer already exists and restating it is a
second place to get it wrong.

Names are deterministic
-----------------------
Page and visual identifiers are UUID5-derived, so regenerating produces
byte-identical output. Random ids would make every deploy look like a rewritten
report and discard per-visual state such as bookmarks.

Usage:
    python generate_report.py --specs ./01_demo-project --out ./01_demo-project/generated/reports
    python generate_report.py --specs ./01_demo-project --out ... --check
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _specs import load as load_spec

NAMESPACE = uuid.UUID("6f9619ff-8b86-d011-b42d-00c04fc964ff")

SCHEMA = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition"

# Declared once. These versions appear both in the $schema URLs and in
# report.json's reportVersionAtImport, and the import fails if the two
# disagree -- so they cannot be written out twice.
VERSIONS = {"report": "2.0.0", "page": "2.0.0", "visual": "2.0.0"}

REPORT_SCHEMA = f"{SCHEMA}/report/{VERSIONS['report']}/schema.json"
PAGE_SCHEMA = f"{SCHEMA}/page/{VERSIONS['page']}/schema.json"
PAGES_SCHEMA = f"{SCHEMA}/pagesMetadata/1.0.0/schema.json"
VISUAL_SCHEMA = f"{SCHEMA}/visualContainer/{VERSIONS['visual']}/schema.json"
PBIR_SCHEMA = ("https://developer.microsoft.com/json-schemas/fabric/item/report/"
               "definitionProperties/2.0.0/schema.json")

# Stock Power BI base theme. Named explicitly because themeCollection is a
# required property -- a report cannot simply decline to state a theme.
BASE_THEME = "CY24SU10"


def ident(*path: str) -> str:
    """A stable 20-hex-character identifier, the shape PBIR uses."""
    return uuid.uuid5(NAMESPACE, "/".join(path)).hex[:20]


def model_fields(model: dict) -> dict[str, str]:
    """Map 'Table.Field' -> 'Measure' | 'Column' from the semantic model.

    The report says what it wants; the model says what that is. Resolving here
    means a report cannot disagree with the model about whether something is a
    measure -- it either resolves or the build fails.
    """
    kinds: dict[str, str] = {}
    for table in model.get("tables", []):
        for column in table.get("columns") or []:
            kinds[f"{table['name']}.{column.get('name', column['source'])}"] = "Column"
    for measure in model.get("measures") or []:
        kinds[f"{measure['table']}.{measure['name']}"] = "Measure"
    return kinds


def field_expression(reference: str, kinds: dict[str, str]) -> dict:
    table, field = reference.split(".", 1)
    kind = kinds.get(reference, "Column")
    return {
        kind: {
            "Expression": {"SourceRef": {"Entity": table}},
            "Property": field,
        }
    }


def projection(reference: str, kinds: dict[str, str], active: bool | None = None) -> dict:
    entry = {"field": field_expression(reference, kinds), "queryRef": reference}
    if active is not None:
        entry["active"] = active
    return entry


def visual_json(visual: dict, page: str, index: int, kinds: dict[str, str]) -> dict:
    position = dict(visual["position"])
    position.setdefault("z", 1000 + index)
    position["tabOrder"] = 1000 + index

    query_state = {}
    for role, references in (visual.get("fields") or {}).items():
        query_state[role] = {
            "projections": [projection(r, kinds) for r in references]
        }

    body: dict = {"visualType": visual["type"], "drillFilterOtherVisuals": True}
    if query_state:
        query: dict = {"queryState": query_state}
        if visual.get("sort"):
            query["sortDefinition"] = {"sort": [{
                "field": field_expression(visual["sort"]["field"], kinds),
                "direction": visual["sort"].get("direction", "Descending"),
            }]}
        body["query"] = query

    # The title is a visual object property, not a query field.
    if visual.get("title"):
        body["objects"] = {"title": [{"properties": {
            "text": {"expr": {"Literal": {"Value": f"'{visual['title']}'"}}},
            "show": {"expr": {"Literal": {"Value": "true"}}},
        }}]}

    document = {
        "$schema": VISUAL_SCHEMA,
        "name": ident(page, "visual", str(index)),
        "position": position,
        "visual": body,
    }

    if visual.get("filters"):
        document["filterConfig"] = {"filters": [
            {"name": ident(page, "visual", str(index), "filter", f["field"]),
             "field": field_expression(f["field"], kinds),
             "type": f.get("type", "Categorical"),
             "howCreated": "User"}
            for f in visual["filters"]]}

    return document


def build(spec: dict, model: dict) -> dict[str, dict[str, str]]:
    """Return {report name: {relative path: file text}}."""
    kinds = model_fields(model)
    defaults = spec.get("defaults") or {}
    output: dict[str, dict[str, str]] = {}

    def dump(value) -> str:
        return json.dumps(value, indent=2) + "\n"

    for report in spec["reports"]:
        files: dict[str, str] = {}
        page_ids = []

        for page in report["pages"]:
            page_id = ident(report["name"], "page", page["name"])
            page_ids.append(page_id)

            files[f"definition/pages/{page_id}/page.json"] = dump({
                "$schema": PAGE_SCHEMA,
                "name": page_id,
                "displayName": page.get("display_name", page["name"]),
                "displayOption": "FitToPage",
                "width": page.get("width", defaults.get("page_width", 1280)),
                "height": page.get("height", defaults.get("page_height", 720)),
            })

            for index, visual in enumerate(page["visuals"]):
                document = visual_json(visual, f"{report['name']}/{page['name']}",
                                       index, kinds)
                files[f"definition/pages/{page_id}/visuals/{document['name']}"
                      f"/visual.json"] = dump(document)

        files["definition/pages/pages.json"] = dump({
            "$schema": PAGES_SCHEMA,
            "pageOrder": page_ids,
            "activePageName": page_ids[0],
        })

        # themeCollection is REQUIRED even when the report uses the stock
        # theme; without it the import fails on schema validation alone.
        # resourcePackages must then declare the theme it names.
        theme = defaults.get("theme", BASE_THEME)
        files["definition/report.json"] = dump({
            "$schema": REPORT_SCHEMA,
            "themeCollection": {
                # A string at schema 2.0.0. The per-part object form
                # ({visual, report, page}) belongs to 3.1.0 and is rejected here.
                "baseTheme": {"name": theme, "type": "SharedResources",
                              "reportVersionAtImport": VERSIONS["report"]}
            },
            "resourcePackages": [{
                "name": "SharedResources",
                "type": "SharedResources",
                "items": [{"name": theme,
                           "path": f"BaseThemes/{theme}.json",
                           "type": "BaseTheme"}],
            }],
            "settings": {
                "useStylableVisualContainerHeader": True,
                "defaultDrillFilterOtherVisuals": True,
            },
        })

        files["definition/version.json"] = dump({
            "$schema": f"{SCHEMA}/versionMetadata/1.0.0/schema.json",
            "version": "2.0.0",
        })

        # Rebound to the model's real id at deploy time.
        files["definition.pbir"] = dump({
            "$schema": PBIR_SCHEMA,
            "version": "4.0",
            "datasetReference": {
                "byConnection": {"connectionString": "semanticmodelid=@@modelid@@"}
            },
        })

        files[".platform"] = dump({
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/"
                       "gitIntegration/platformProperties/2.0.0/schema.json",
            "metadata": {"type": "Report",
                         "displayName": report.get("display_name", report["name"]),
                         "description": " ".join(
                             report.get("description", "").split())[:250]},
            "config": {"version": "2.0", "logicalId": str(
                uuid.uuid5(NAMESPACE, f"report/{report['name']}"))},
        })

        output[report["name"]] = files

    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--specs", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    root = Path(args.specs)
    spec = load_spec(root, "reports", track="powerbi")
    model = load_spec(root, "semantic-model", track="powerbi")

    kinds = model_fields(model)
    missing = []
    for report in spec["reports"]:
        for page in report["pages"]:
            for visual in page["visuals"]:
                for references in (visual.get("fields") or {}).values():
                    missing += [r for r in references if r not in kinds]
                if visual.get("sort") and visual["sort"]["field"] not in kinds:
                    missing.append(visual["sort"]["field"])
    if missing:
        print("  ERROR  these fields are not in the semantic model:")
        for reference in sorted(set(missing)):
            print(f"           {reference}")
        print("         A visual bound to a field that does not exist renders "
              "empty rather than\n         failing, so this is refused here.")
        return 1

    out = Path(args.out)
    planned = build(spec, model)

    if args.check:
        drifted = []
        for name, files in planned.items():
            for path, text in files.items():
                target = out / name / path
                if not target.exists() or target.read_text(encoding="utf-8") != text:
                    drifted.append(f"{name}/{path}")
        if drifted:
            print("Generated reports are out of date with the spec:")
            for path in drifted:
                print(f"  {path}")
            return 1
        print(f"All {len(planned)} reports match the spec.")
        return 0

    for name, files in planned.items():
        target = out / name
        if target.exists():
            shutil.rmtree(target)
        for path, text in files.items():
            full = target / path
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(text, encoding="utf-8")

    print(f"Generated {len(planned)} reports in {out}")
    for report in spec["reports"]:
        visuals = sum(len(p["visuals"]) for p in report["pages"])
        print(f"  {report['name']}  ({len(report['pages'])} pages, {visuals} visuals)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
