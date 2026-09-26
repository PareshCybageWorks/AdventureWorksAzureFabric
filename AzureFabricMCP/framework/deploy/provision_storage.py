"""
Create a workspace's storage items from the F1 scaffolding spec.

create_workspaces.py creates the workspace shell. This fills it: the lakehouses,
the warehouse and the Spark environment the rest of the pipeline assumes exist.

Without this, an environment beyond the first can only be built by hand, which
is exactly the thing the framework claims not to require -- and it is the step
that blocks a CI deploy to a fresh qa or prod workspace.

Idempotent. An item that already exists is left alone and its id reported, so
re-running is how you verify a workspace is complete.

The spec is the authority on what should exist:

    storage.items.medallion  ->  lh_bronze, lh_silver, wh_gold   (or per-domain
    storage.items.mesh           warehouses under a mesh topology)

Usage:
    python provision_storage.py --project ./01_demo-project --env qa
    python provision_storage.py --project ./01_demo-project --env qa --dry-run
    python provision_storage.py --project ./01_demo-project --all-environments
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import requests
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _project import get_environment, get_workspace_id  # noqa: E402
import _tsql  # noqa: E402

FABRIC_API = "https://api.fabric.microsoft.com/v1"

# Spec item kind -> (Fabric item type, REST collection)
ITEM_TYPES = {
    "lakehouse": ("Lakehouse", "lakehouses"),
    "warehouse": ("Warehouse", "warehouses"),
    "environment": ("Environment", "environments"),
}

# The Spark Environment carrying the ttfabric wheel. Not part of `storage`
# because it is compute, but every notebook binds to it, so a workspace without
# it cannot run anything.
SPARK_ENVIRONMENT = "env_spark"


def planned_items(scaffolding: dict) -> list[tuple[str, str]]:
    """Return [(kind, name)] the spec says this workspace should hold."""
    topology = (scaffolding.get("topology") or {})
    style = topology.get("style") or topology.get("kind") or "medallion"
    items_spec = (scaffolding.get("storage") or {}).get("items") or {}

    planned: list[tuple[str, str]] = []

    if style == "medallion":
        for layer in ("bronze", "silver", "gold"):
            entry = (items_spec.get("medallion") or {}).get(layer)
            if entry and entry.get("name"):
                planned.append((entry.get("item", "lakehouse"), entry["name"]))
    else:
        # Mesh: one item per declared domain, named from the template.
        template = ((items_spec.get("mesh") or {}).get("per_domain") or {})
        for domain in scaffolding.get("domains") or []:
            name = domain["name"] if isinstance(domain, dict) else domain
            planned.append((template.get("item", "warehouse"),
                            template.get("name", "wh_{domain}").format(domain=name)))

    planned.append(("environment", SPARK_ENVIRONMENT))
    return planned


def existing(headers: dict, workspace: str) -> dict[tuple[str, str], str]:
    """{(type, displayName): id} for everything already in the workspace."""
    found: dict[tuple[str, str], str] = {}
    response = _tsql.with_retry("GET", f"{FABRIC_API}/workspaces/{workspace}/items",
                                headers=headers, timeout=90)
    for item in response.json().get("value", []):
        found[(item["type"], item["displayName"])] = item["id"]
    return found


def create(headers: dict, workspace: str, kind: str, name: str,
           name_wait_attempts: int = 10, name_wait_seconds: int = 30) -> tuple[bool, str]:
    item_type, collection = ITEM_TYPES[kind]

    for attempt in range(1, name_wait_attempts + 1):
        response = _tsql.with_retry(
            "POST", f"{FABRIC_API}/workspaces/{workspace}/{collection}",
            headers=headers, json={"displayName": name}, timeout=180)

        # Fabric reserves the name of a DELETED item for a while before it can
        # be reused. Clearing a workspace and immediately rebuilding it -- the
        # obvious way to reset for a demo -- therefore fails on every item,
        # with an error that reads like a naming conflict rather than a wait.
        if response.status_code == 409 and "NotAvailableYet" in response.text:
            if attempt == name_wait_attempts:
                return False, (f"name still reserved from a recent delete after "
                               f"{name_wait_attempts * name_wait_seconds}s. Fabric "
                               f"releases it on its own schedule; try again later.")
            print(f"    {name}: name still held from a recent delete, "
                  f"waiting {name_wait_seconds}s ({attempt}/{name_wait_attempts})")
            time.sleep(name_wait_seconds)
            continue
        break

    if response.status_code in (200, 201):
        return True, response.json().get("id", "")

    # A warehouse and a lakehouse both provision asynchronously.
    if response.status_code == 202:
        location = response.headers.get("Location")
        for _ in range(40):
            time.sleep(5)
            poll = _tsql.with_retry("GET", location, headers=headers, timeout=60).json()
            if poll.get("status") == "Succeeded":
                return True, poll.get("id") or ""
            if poll.get("status") == "Failed":
                return False, str(poll.get("error", ""))[:200]
        return False, "still provisioning after 200s"

    return False, f"HTTP {response.status_code}: {response.text[:200]}"


def provision_folders(headers: dict, workspace: str, scaffolding: dict,
                      dry_run: bool) -> int:
    """Create the workspace folders the spec declares, parents before children.

    organise_items.py files items into these but deliberately does not create
    them -- conjuring a folder because a pattern matched nothing would paper
    over a spec error. Nothing else created them either, so dev's were made by
    hand and a fresh workspace had none at all: every placement would report
    "folder missing" and every item would stay at the root.

    Idempotent. An existing folder is left alone.
    """
    declared = scaffolding.get("workspace_folders") or []
    if not declared:
        return 0

    response = _tsql.with_retry("GET", f"{FABRIC_API}/workspaces/{workspace}/folders",
                                headers=headers, timeout=90)
    existing = {f["displayName"]: f["id"] for f in response.json().get("value", [])
                if not f.get("parentFolderId")}
    # Child names repeat -- there is a `notebook` folder under each layer -- so
    # they are keyed by parent.
    children = {(f.get("parentFolderId"), f["displayName"]): f["id"]
                for f in response.json().get("value", [])}

    failures = 0

    def create(name: str, parent: str | None) -> str | None:
        body: dict = {"displayName": name}
        if parent:
            body["parentFolderId"] = parent
        made = _tsql.with_retry("POST", f"{FABRIC_API}/workspaces/{workspace}/folders",
                                headers=headers, json=body, timeout=90)
        if made.status_code in (200, 201):
            return made.json().get("id")
        print(f"  FAILED  folder {name}  {made.status_code}: {made.text[:120]}")
        return None

    for folder in declared:
        name = folder["name"]
        parent_id = existing.get(name)
        if parent_id:
            print(f"  exists  folder       {name}")
        elif dry_run:
            print(f"  create  folder       {name}")
            parent_id = None
        else:
            parent_id = create(name, None)
            failures += parent_id is None
            if parent_id:
                print(f"  CREATED folder       {name}")

        for sub in folder.get("subfolders") or []:
            label = f"{name}/{sub['name']}"
            if parent_id and (parent_id, sub["name"]) in children:
                print(f"  exists  folder       {label}")
                continue
            if dry_run:
                print(f"  create  folder       {label}")
                continue
            if not parent_id:
                print(f"  SKIPPED folder       {label} (parent not created)")
                failures += 1
                continue
            made = create(sub["name"], parent_id)
            print(f"  {'CREATED' if made else 'FAILED '} folder       {label}")
            failures += made is None

    return failures


def provision(headers: dict, project: Path, env: str, dry_run: bool) -> int:
    environment = get_environment(project, env)
    workspace = get_workspace_id(project, env)
    scaffolding = yaml.safe_load(
        (project / "fabric" / "01-scaffolding.yaml").read_text(encoding="utf-8"))

    print(f"environment {env}  ->  {environment.get('workspace')}  ({workspace})")

    planned = planned_items(scaffolding)
    present = existing(headers, workspace)
    failures = 0

    for kind, name in planned:
        item_type = ITEM_TYPES[kind][0]
        if (item_type, name) in present:
            print(f"  exists  {item_type:<12} {name}  ({present[(item_type, name)]})")
            continue
        if dry_run:
            print(f"  create  {item_type:<12} {name}")
            continue

        ok, detail = create(headers, workspace, kind, name)
        if ok:
            print(f"  CREATED {item_type:<12} {name}  ({detail})")
        else:
            print(f"  FAILED  {item_type:<12} {name}  {detail}")
            failures += 1

    failures += provision_folders(headers, workspace, scaffolding, dry_run)

    if not dry_run and any(k == "environment" for k, _ in planned):
        print("  note    env_spark has no library yet -- run push_library.py "
              "before any notebook")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--env", default="dev")
    parser.add_argument("--all-environments", action="store_true",
                        help="provision every environment declared in F1")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    project = Path(args.project).resolve()
    scaffolding_path = project / "fabric" / "01-scaffolding.yaml"
    if not scaffolding_path.exists():
        print(f"  ERROR  no scaffolding spec at {scaffolding_path}")
        return 2

    if args.all_environments:
        scaffolding = yaml.safe_load(scaffolding_path.read_text(encoding="utf-8"))
        targets = [e["name"] for e in scaffolding.get("environments", [])
                   if e.get("workspace_id")]
        if not targets:
            print("  ERROR  no environment has a workspace_id. "
                  "Run create_workspaces.py first.")
            return 2
    else:
        targets = [args.env]

    headers = {"Authorization": f"Bearer "
                                f"{_tsql.credential().get_token(_tsql.FABRIC_SCOPE).token}",
               "Content-Type": "application/json"}

    failures = 0
    for index, env in enumerate(targets):
        if index:
            print()
        try:
            failures += provision(headers, project, env, args.dry_run)
        except (KeyError, ValueError, FileNotFoundError) as exc:
            print(f"  ERROR  {env}: {exc}")
            failures += 1

    print()
    if args.dry_run:
        print("dry run -- nothing created")
    elif failures:
        print(f"{failures} item(s) could not be created")
    else:
        print("every declared item is present")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
