"""
Create the environment workspaces a project's spec declares.

Reads 01-environments.yaml and ensures one Fabric workspace per environment,
named exactly as the spec says. Idempotent: an existing workspace with the
target name is adopted rather than duplicated, and its id is reported so it can
be recorded back into the spec.

`--rename-from` handles the common starting point where a template workspace
already holds real work. Renaming preserves every item, folder and uploaded
file; creating a fresh workspace and rebuilding would not.

Nothing here is destructive. The script creates, renames and assigns capacity;
it never deletes a workspace, so a mistake costs an unused workspace rather
than someone's work.

Usage:
    python create_workspaces.py --project ./01_demo-project --dry-run
    python create_workspaces.py --project ./01_demo-project \
        --capacity 325b3b5d-... --rename-from 13c508c8-...=dev
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import requests
import yaml

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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--capacity", help="capacity id to assign; defaults to the "
                                           "capacity of the first existing workspace")
    parser.add_argument("--rename-from", action="append", default=[],
                        metavar="WORKSPACE_ID=ENV",
                        help="rename an existing workspace to serve an environment, "
                             "preserving its contents")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    project = Path(args.project).resolve()

    # Resolve through _project, not by reading a path directly.
    #
    # This script hard-coded specs/01-environments.yaml, which is the LEGACY
    # layout. _project exists to resolve either layout new-first, and its own
    # docstring says every deploy script goes through it -- this one did not,
    # so a project scaffolded by the current new_project.py (which creates
    # fabric/, powerbi/, dataops/, cicd/ and no specs/) could not be
    # provisioned at all. It failed with FileNotFoundError naming a file the
    # scaffolder has never produced, which reads like a corrupt project rather
    # than a stale script.
    from _project import load_scaffolding

    environments, layout = load_scaffolding(project)
    print(f"spec     {layout}")

    renames = {}
    for pair in args.rename_from:
        workspace_id, _, env_name = pair.partition("=")
        renames[env_name] = workspace_id

    headers = {"Authorization": f"Bearer {get_token()}", "Content-Type": "application/json"}

    existing = requests.get(f"{FABRIC_API}/workspaces", headers=headers, timeout=60, verify=False)
    existing.raise_for_status()
    by_name = {w["displayName"]: w for w in existing.json().get("value", [])}
    by_id = {w["id"]: w for w in existing.json().get("value", [])}

    # Capacity resolution, most specific first. Falling back to "whatever
    # workspace we saw first" is actively dangerous: it silently picked an F2
    # (2 CU) here, which cannot run Spark at any useful speed, and the failure
    # would surface later as an unexplained slow notebook.
    capacity = args.capacity
    capacity_source = "--capacity"
    if not capacity and renames:
        source_id = next(iter(renames.values()))
        capacity = by_id.get(source_id, {}).get("capacityId")
        capacity_source = f"inherited from {source_id}"
    if not capacity:
        print("  ERROR  no capacity given and none could be inferred.")
        print("         Pass --capacity explicitly; picking one arbitrarily risks")
        print("         landing Spark workloads on a capacity too small to run them.")
        return 2

    print(f"capacity: {capacity}  ({capacity_source})")
    if args.dry_run:
        print("dry run -- nothing will be created or renamed")
    print()

    results: dict[str, str] = {}
    created = renamed = adopted = failed = 0

    for env in environments["environments"]:
        name = env["workspace"]
        env_name = env["name"]

        # -- already correctly named ---------------------------------------
        if name in by_name:
            workspace_id = by_name[name]["id"]
            print(f"  EXISTS   {name:<24} {workspace_id}")
            results[env_name] = workspace_id
            adopted += 1
            continue

        # -- rename an existing workspace ----------------------------------
        source_id = renames.get(env_name)
        if source_id:
            if source_id not in by_id:
                print(f"  FAIL     {name:<24} rename source {source_id} not found")
                failed += 1
                continue
            current = by_id[source_id]["displayName"]
            if args.dry_run:
                print(f"  would rename {current!r} -> {name!r}  ({source_id})")
                results[env_name] = source_id
                continue
            response = requests.patch(f"{FABRIC_API}/workspaces/{source_id}",
                                      headers=headers, json={"displayName": name},
                                      timeout=60, verify=False)
            if response.ok:
                print(f"  RENAMED  {current!r} -> {name:<20} {source_id}")
                results[env_name] = source_id
                renamed += 1
            else:
                print(f"  FAIL     {name:<24} HTTP {response.status_code}: "
                      f"{response.text[:200]}")
                failed += 1
            continue

        # -- create ---------------------------------------------------------
        if args.dry_run:
            print(f"  would create {name}")
            continue

        body = {"displayName": name,
                "description": f"{env_name} environment for "
                               f"{environments['metadata']['name']}"}
        if capacity:
            body["capacityId"] = capacity

        response = requests.post(f"{FABRIC_API}/workspaces", headers=headers,
                                 json=body, timeout=120, verify=False)
        if response.ok:
            workspace_id = response.json()["id"]
            print(f"  CREATED  {name:<24} {workspace_id}")
            results[env_name] = workspace_id
            created += 1
        else:
            print(f"  FAIL     {name:<24} HTTP {response.status_code}: "
                  f"{response.text[:200]}")
            failed += 1

    print()
    print(f"{created} created, {renamed} renamed, {adopted} already existed, "
          f"{failed} failed")

    if results and not args.dry_run:
        print()
        print("Record these in specs/01-environments.yaml:")
        for env_name, workspace_id in results.items():
            print(f"    {env_name}: workspace_id: {workspace_id}")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
