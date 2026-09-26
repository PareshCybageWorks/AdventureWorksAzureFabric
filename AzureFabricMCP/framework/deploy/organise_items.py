"""
File workspace items into the folders their spec declares.

Item creation cannot set a folder -- neither the MCP nor the REST create-item
call accepts one -- so every item is born at the workspace root. This is the
separate step that puts each one where 00-platform.yaml `workspace_folders`
says it belongs.

Idempotent: an item already in its target folder is left alone, so this can run
after every deployment without churn.

Folders are matched by PATH ("1_bronze/notebook"), not by name, because the
subfolder names repeat -- there is a `notebook` folder under each layer, and
matching on the leaf alone would file silver notebooks under bronze.

Missing folders are reported, not created. The folder taxonomy is a deliberate
structure someone designed; silently conjuring a folder because a pattern
matched nothing would paper over a spec error.

Usage:
    python organise_items.py --project ./01_demo-project --env dev
    python organise_items.py --project ./01_demo-project --env dev --dry-run
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import sys
from pathlib import Path

import requests
import yaml

# Sibling import: deploy scripts are run as files, not as a package.
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "generators"))
from _project import get_environment, get_storage_ids, get_workspace_id
from _specs import load as load_spec
import _tsql

FABRIC_API = "https://api.fabric.microsoft.com/v1"
SCOPE = "https://api.fabric.microsoft.com/.default"


def get_token() -> str:
    from azure.identity import AzureCliCredential, ClientSecretCredential

    client_id = os.getenv("AZURE_CLIENT_ID") or os.getenv("FABRIC_CLIENT_ID")
    secret = os.getenv("AZURE_CLIENT_SECRET") or os.getenv("FABRIC_CLIENT_SECRET")
    tenant = os.getenv("AZURE_TENANT_ID") or os.getenv("FABRIC_TENANT_ID")

    if client_id and secret and tenant:
        print("auth: service principal")
        return ClientSecretCredential(tenant, client_id, secret).get_token(SCOPE).token
    print("auth: az cli session")
    return AzureCliCredential().get_token(SCOPE).token


def folder_paths(folders: list[dict]) -> dict[str, str]:
    """Map "parent/child" -> folder id.

    Fabric returns a flat list with parentFolderId, so the path is rebuilt by
    walking upwards. Paths disambiguate the repeated `notebook` and `pipeline`
    subfolder names.
    """
    by_id = {f["id"]: f for f in folders}

    def path_of(folder: dict) -> str:
        segments = [folder["displayName"]]
        parent_id = folder.get("parentFolderId")
        while parent_id and parent_id in by_id:
            parent = by_id[parent_id]
            segments.append(parent["displayName"])
            parent_id = parent.get("parentFolderId")
        return "/".join(reversed(segments))

    return {path_of(f): f["id"] for f in folders}


def planned_placements(platform: dict) -> list[tuple[str, str, str]]:
    """Yield (folder_path, item_type, name_or_pattern) from the spec."""
    plan: list[tuple[str, str, str]] = []

    for folder in platform.get("workspace_folders", []):
        parent = folder["name"]

        for entry in folder.get("contains", []):
            plan.append((parent, entry["item_type"],
                         entry.get("name_pattern") or entry["name"]))

        for sub in folder.get("subfolders", []):
            path = f"{parent}/{sub['name']}"
            for entry in sub.get("contains", []):
                plan.append((path, entry["item_type"],
                             entry.get("name_pattern") or entry["name"]))

    return plan


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--env", default="dev")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    project = Path(args.project).resolve()

    # Resolved through _specs, like every other generator and deploy script.
    #
    # This read `specs/00-platform.yaml` directly, which is the LEGACY spec.
    # Both files exist during the migration, so it kept working and kept
    # reading the stale one -- the authoritative folder taxonomy in
    # fabric/01-scaffolding.yaml was ignored for as long as the two agreed.
    # When 6_powerbi was added to the real spec, this filed twenty items,
    # reported "0 unmatched", and left the four new ones at the workspace root.
    platform = load_spec(project, "scaffolding")
    try:
        env = get_environment(project, args.env)
        workspace = get_workspace_id(project, args.env)
    except (FileNotFoundError, KeyError, ValueError) as exc:
        print(f"  ERROR  {exc}")
        return 2

    print(f"environment {args.env}  ->  {env.get('workspace')}  ({workspace})")
    if args.dry_run:
        print("dry run -- nothing will be moved")
    print()

    headers = {"Authorization": f"Bearer {get_token()}", "Content-Type": "application/json"}

    folders = _tsql.with_retry("GET", f"{FABRIC_API}/workspaces/{workspace}/folders",
                           headers=headers, timeout=60)
    folders.raise_for_status()
    paths = folder_paths(folders.json().get("value", []))

    items = _tsql.with_retry("GET", f"{FABRIC_API}/workspaces/{workspace}/items",
                         headers=headers, timeout=60)
    items.raise_for_status()
    all_items = items.json().get("value", [])

    # A Report is DEPLOYED under its `display_name`, not its spec `name`.
    #
    # P2 declares both -- `name: rpt_itsm_operations` for the naming convention
    # and `display_name: ITSM Operations` for the people who open it -- and
    # push_reports uses the display name. So a folder entry written against
    # `naming.report` (rpt_*), which is what F1 declares the convention to be,
    # matched nothing and the report stayed at the workspace root.
    #
    # Resolving the deployed name back to the spec name lets a folder be
    # declared in the framework's own convention rather than against whatever
    # prose someone chose for the title.
    spec_names: dict[str, str] = {}
    try:
        reports = load_spec(project, "reports", track="powerbi") or {}
    except (FileNotFoundError, KeyError):
        reports = {}          # a project without reports is fine
    for entry in reports.get("reports", []) or []:
        if entry.get("display_name") and entry.get("name"):
            spec_names[entry["display_name"]] = entry["name"]

    def name_matches(item: dict, pattern: str) -> bool:
        deployed = item["displayName"]
        if fnmatch.fnmatch(deployed, pattern):
            return True
        alias = spec_names.get(deployed)
        return bool(alias and fnmatch.fnmatch(alias, pattern))

    # An item's current folder is not returned by the list call, so a move is
    # issued regardless; the API is idempotent for an item already in place.
    moved = already = missing = failed = 0

    for folder_path, item_type, pattern in planned_placements(platform):
        target = paths.get(folder_path)
        if target is None:
            print(f"  NO FOLDER  {folder_path}  (declared in spec, absent in workspace)")
            missing += 1
            continue

        matches = [i for i in all_items
                   if i["type"] == item_type and name_matches(i, pattern)]
        if not matches:
            print(f"  NO ITEM    {item_type} {pattern}  (nothing matches)")
            missing += 1
            continue

        for item in matches:
            if args.dry_run:
                print(f"  would move {item['displayName']:<26} -> {folder_path}")
                continue
            response = _tsql.with_retry(
                "POST",
                f"{FABRIC_API}/workspaces/{workspace}/items/{item['id']}/move",
                headers=headers, json={"targetFolderId": target}, timeout=60)
            if response.ok:
                print(f"  OK         {item['displayName']:<26} -> {folder_path}")
                moved += 1
            else:
                print(f"  FAIL       {item['displayName']:<26} -> {folder_path}"
                      f"  HTTP {response.status_code}: {response.text[:160]}")
                failed += 1

    print()
    print(f"{moved} filed, {already} already in place, "
          f"{missing} unmatched, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
