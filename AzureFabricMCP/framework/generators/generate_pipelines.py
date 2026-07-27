"""
Generate Fabric Data Pipeline definitions from a project's specs.

One pipeline per layer plus a master orchestrator. Activity dependencies are
derived from the specs, not hand-drawn:

  bronze   no inter-entity dependency -- all four land in parallel
  silver   `depends_on` in mappings/silver.yaml
  gold     dimensions first, then facts, because a fact resolves the
           dimensions' surrogate keys
  master   bronze -> silver -> gold, stopping on the first failure

Environment portability
-----------------------
A Fabric pipeline references notebooks by GUID, and those GUIDs differ per
environment. Baking dev's ids into the generated file would make the artefact
non-portable and its diff churn on every environment.

So generation emits a PLACEHOLDER:

    "notebookId": "@@notebook:nb_load_customers@@"

and deploy/push_items.py substitutes the real id, resolved by display name from
the target workspace, at push time. Generation stays deterministic; the same
file promotes to qa, uat and prod unchanged.

Usage:
    python generate_pipelines.py --specs ./specs --out ./generated/pipelines
    python generate_pipelines.py --specs ./specs --out ./generated/pipelines --check
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

# Sibling import: generators are run as files, not as a package.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _specs import load as load_spec

NOTEBOOK_REF = "@@notebook:{name}@@"
PIPELINE_REF = "@@pipeline:{name}@@"
WORKSPACE_REF = "@@workspace@@"

# Fabric activity type names.
NOTEBOOK_ACTIVITY = "TridentNotebook"
PIPELINE_ACTIVITY = "ExecutePipeline"


def notebook_activity(name: str, depends_on: list[str] | None = None) -> dict:
    return {
        "name": name,
        "type": NOTEBOOK_ACTIVITY,
        "dependsOn": [
            {"activity": upstream, "dependencyConditions": ["Succeeded"]}
            for upstream in (depends_on or [])
        ],
        "policy": {
            "timeout": "0.02:00:00",
            "retry": 1,
            "retryIntervalInSeconds": 60,
            # A notebook that already wrote half its output should not be
            # silently re-run by a secure retry.
            "secureInput": False,
            "secureOutput": False,
        },
        "typeProperties": {
            "notebookId": NOTEBOOK_REF.format(name=name),
            "workspaceId": WORKSPACE_REF,
            "parameters": {},
        },
    }


def pipeline_activity(name: str, depends_on: list[str] | None = None) -> dict:
    """A master-pipeline step that runs another pipeline in the same workspace.

    Uses ExecutePipeline rather than Fabric's newer InvokePipeline. InvokePipeline
    requires an `externalReferences.connection` -- it is built for calling
    pipelines across workspaces or tenants, and rejects a definition without one
    ("'ExternalReferences' cannot be null"). ExecutePipeline targets a pipeline
    in the same workspace and needs no connection, which is exactly this case.
    """
    return {
        "name": name,
        "type": PIPELINE_ACTIVITY,
        "dependsOn": [
            {"activity": upstream, "dependencyConditions": ["Succeeded"]}
            for upstream in (depends_on or [])
        ],
        "typeProperties": {
            "pipeline": {
                "referenceName": PIPELINE_REF.format(name=name),
                "type": "PipelineReference",
            },
            # Block so the master sequences its stages rather than firing all
            # three at once and letting silver read a half-written bronze.
            "waitOnCompletion": True,
            "parameters": {},
        },
    }


def wrap(activities: list[dict]) -> dict:
    return {"properties": {"activities": activities}}


def render(definition: dict) -> str:
    return json.dumps(definition, indent=2, sort_keys=True) + "\n"


def build_bronze(platform: dict, sources: dict) -> dict:
    verb = platform["naming"]["verbs"]["bronze"]
    template = platform["naming"]["notebook"]
    activities = [
        notebook_activity(template.format(verb=verb, layer="bronze", entity=entity["name"]))
        for source in sources["sources"]
        for entity in source["entities"]
    ]
    return wrap(activities)


def build_silver(platform: dict, silver: dict) -> dict:
    verb = platform["naming"]["verbs"]["silver"]
    template = platform["naming"]["notebook"]

    def nb(target: str) -> str:
        return template.format(verb=verb, layer="silver", entity=target)

    activities = []
    for table in silver["tables"]:
        # depends_on names silver TARGETS; map them to their notebook names.
        upstream = [nb(dep) for dep in table.get("depends_on", [])]
        activities.append(notebook_activity(nb(table["target"]), upstream))

    # The cascade runs last, after EVERY table. It cannot sit next to either
    # side of a parent/child pair: a parent is often cleansed after its child
    # (an order header is corrected from its lines), so until the whole layer
    # is built it is not known which parents survived.
    if silver.get("cascade_quarantine"):
        activities.append(notebook_activity(
            "nb_cascade_quarantine",
            [nb(t["target"]) for t in silver["tables"]]))

    return wrap(activities)


def build_gold(platform: dict, gold: dict) -> dict:
    verb = platform["naming"]["verbs"]["gold"]
    template = platform["naming"]["notebook"]

    def nb(name: str) -> str:
        return template.format(verb=verb, layer="gold", entity=name)

    dimension_names = [nb(d["name"]) for d in gold.get("dimensions", [])]
    activities = [notebook_activity(name) for name in dimension_names]

    for fact in gold.get("facts", []):
        # A fact resolves every dimension's surrogate key, so it waits on all
        # of them rather than only the ones it names -- a dimension still
        # building would yield unresolved keys that the unknown member would
        # quietly absorb.
        activities.append(notebook_activity(nb(fact["name"]), dimension_names))
    return wrap(activities)


def build_master(platform: dict) -> dict:
    pipelines = platform["naming"]["pipelines"]
    order = ["bronze", "silver", "gold"]

    activities = []
    previous: list[str] = []
    for layer in order:
        name = pipelines[layer]
        activities.append(pipeline_activity(name, previous))
        previous = [name]
    return wrap(activities)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--specs", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    specs = Path(args.specs)
    out = Path(args.out)

    platform = load_spec(specs, "scaffolding")
    sources = load_spec(specs, "sources")
    silver = load_spec(specs, "silver")
    gold = load_spec(specs, "gold")

    names = platform["naming"]["pipelines"]
    planned = {
        out / f"{names['bronze']}.json": render(build_bronze(platform, sources)),
        out / f"{names['silver']}.json": render(build_silver(platform, silver)),
        out / f"{names['gold']}.json": render(build_gold(platform, gold)),
        out / f"{names['master']}.json": render(build_master(platform)),
    }

    if args.check:
        drifted = [p.name for p, body in planned.items()
                   if not p.exists() or p.read_text(encoding="utf-8") != body]
        if drifted:
            print("Generated pipelines are out of date with the specs:")
            for name in drifted:
                print(f"  {name}")
            return 1
        print(f"All {len(planned)} pipelines match the specs.")
        return 0

    out.mkdir(parents=True, exist_ok=True)
    for path, body in planned.items():
        path.write_text(body, encoding="utf-8")

    print(f"Generated {len(planned)} pipelines in {out}")
    for path in sorted(planned):
        definition = json.loads(planned[path])
        activities = definition["properties"]["activities"]
        print(f"  {path.name}  ({len(activities)} activities)")
        for activity in activities:
            deps = [d["activity"] for d in activity["dependsOn"]]
            arrow = f"  <- {', '.join(deps)}" if deps else ""
            print(f"      {activity['name']}{arrow}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
