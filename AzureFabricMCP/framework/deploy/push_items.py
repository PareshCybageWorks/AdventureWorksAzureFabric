"""
Push generated notebook and pipeline definitions into a Fabric workspace.

Why this exists
---------------
Creating a Fabric item and giving it CONTENT are separate operations. The MCP
tooling can create an item (name, type, description) but carries no definition
payload, so items arrive as empty shells. The Fabric REST API's
`updateDefinition` endpoint is what actually loads the code.

Authentication
--------------
Resolved in this order, so the same script works on a laptop and in CI:

  1. AZURE_CLIENT_ID / AZURE_CLIENT_SECRET / AZURE_TENANT_ID  -> service principal
  2. `az login` session                                       -> developer

Scope is https://api.fabric.microsoft.com/.default in both cases.

Items are matched by DISPLAY NAME, not by a recorded id. Ids drift when an item
is recreated, and a stale id in a spec would silently push code into whatever
item now holds it -- matching by name fails loudly instead.

Usage:
    python push_items.py --project ./01_demo-project --env dev
    python push_items.py --project ./01_demo-project --env dev --dry-run
    python push_items.py --project ./01_demo-project --env dev --only nb_load_customers
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
from pathlib import Path

import requests
import yaml

# Sibling import: deploy scripts are run as files, not as a package.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _project import get_environment, get_storage_ids, get_workspace_id

FABRIC_API = "https://api.fabric.microsoft.com/v1"
SCOPE = "https://api.fabric.microsoft.com/.default"
# Larger notebooks take well over two minutes to commit server-side. A short
# budget reports a timeout as a push failure, which sends you looking for a
# problem in a definition that was actually fine.
POLL_SECONDS = 5
POLL_LIMIT = 120


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def get_token() -> str:
    from azure.identity import AzureCliCredential, ClientSecretCredential

    client_id = os.getenv("AZURE_CLIENT_ID") or os.getenv("FABRIC_CLIENT_ID")
    secret = os.getenv("AZURE_CLIENT_SECRET") or os.getenv("FABRIC_CLIENT_SECRET")
    tenant = os.getenv("AZURE_TENANT_ID") or os.getenv("FABRIC_TENANT_ID")

    if client_id and secret and tenant:
        print("auth: service principal")
        credential = ClientSecretCredential(tenant, client_id, secret)
    else:
        print("auth: az cli session")
        credential = AzureCliCredential()

    return credential.get_token(SCOPE).token


# ---------------------------------------------------------------------------
# REST helpers
# ---------------------------------------------------------------------------

class Fabric:
    def __init__(self, token: str) -> None:
        self.headers = {"Authorization": f"Bearer {token}",
                        "Content-Type": "application/json"}

    def list_items(self, workspace: str) -> list[dict]:
        response = requests.get(f"{FABRIC_API}/workspaces/{workspace}/items",
                                headers=self.headers, timeout=60)
        response.raise_for_status()
        return response.json().get("value", [])

    def create_item(self, workspace: str, display_name: str,
                    item_type: str, description: str = "") -> str | None:
        """Create an empty item so its definition can be pushed into it.

        A spec change that renames a table renames its notebook too, and the
        old item no longer matches anything. Without creation here the push
        silently skips it and the deployment looks clean while the new notebook
        does not exist.
        """
        response = requests.post(
            f"{FABRIC_API}/workspaces/{workspace}/items",
            headers=self.headers,
            json={"displayName": display_name, "type": item_type,
                  "description": description},
            timeout=120)

        if response.status_code in (200, 201):
            return response.json().get("id")
        if response.status_code == 202:
            # Async creation returns no body; resolve the id by name afterwards.
            self.wait(response)
            for item in self.list_items(workspace):
                if item["displayName"] == display_name and item["type"] == item_type:
                    return item["id"]
        print(f"      create failed {response.status_code}: {response.text[:200]}")
        return None

    def wait(self, response: requests.Response) -> bool:
        """Follow a long-running operation to completion.

        updateDefinition returns 202 with no body. Treating that as success
        would report a push that silently failed, so the operation is polled
        to a terminal state.
        """
        if response.status_code not in (202,):
            return response.ok

        location = response.headers.get("Location")
        if not location:
            return True

        for _ in range(POLL_LIMIT):
            time.sleep(POLL_SECONDS)
            poll = requests.get(location, headers=self.headers, timeout=60)
            if not poll.ok:
                return False
            status = poll.json().get("status")
            if status == "Succeeded":
                return True
            if status == "Failed":
                print(f"      operation failed: {poll.json().get('error')}")
                return False
        print("      timed out waiting for the operation")
        return False

    def update_definition(self, workspace: str, item_type: str,
                          item_id: str, body: dict, attempts: int = 3) -> bool:
        """Push a definition, retrying transient failures.

        Pushing a dozen items back to back gets throttled, and a throttled call
        looks exactly like a broken definition: the operation never reaches a
        terminal state and the poll budget expires. Retrying with backoff
        distinguishes the two, so a genuine definition error is the only thing
        that reports as a failure.
        """
        collection = {"Notebook": "notebooks", "DataPipeline": "dataPipelines"}[item_type]
        url = (f"{FABRIC_API}/workspaces/{workspace}/{collection}/{item_id}"
               f"/updateDefinition?updateMetadata=false")

        for attempt in range(1, attempts + 1):
            response = requests.post(url, headers=self.headers, json=body, timeout=180)

            if response.status_code in (429, 503):
                delay = int(response.headers.get("Retry-After", 10 * attempt))
                print(f"      throttled ({response.status_code}); retrying in {delay}s")
                time.sleep(delay)
                continue

            if response.status_code not in (200, 202):
                # A 4xx other than throttling is a bad definition -- retrying
                # would just repeat the same rejection.
                print(f"      HTTP {response.status_code}: {response.text[:300]}")
                return False

            if self.wait(response):
                return True

            if attempt < attempts:
                delay = 5 * attempt
                print(f"      attempt {attempt} did not settle; retrying in {delay}s")
                time.sleep(delay)

        return False


def b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def platform_part(item_type: str, display_name: str) -> str:
    return json.dumps({
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/"
                   "gitIntegration/platformProperties/2.0.0/schema.json",
        "metadata": {"type": item_type, "displayName": display_name},
        "config": {"version": "2.0",
                   "logicalId": "00000000-0000-0000-0000-000000000000"},
    })


def notebook_body(definition_text: str, display_name: str) -> dict:
    # Sent as ipynb; Fabric normalises it to its own .py notebook format on
    # arrival, which is why a round-trip read returns notebook-content.py.
    return {"definition": {"format": "ipynb", "parts": [
        {"path": "notebook-content.ipynb",
         "payload": b64(definition_text),
         "payloadType": "InlineBase64"},
        {"path": ".platform",
         "payload": b64(platform_part("Notebook", display_name)),
         "payloadType": "InlineBase64"},
    ]}}


def resolve_placeholders(text: str, workspace: str,
                         existing: dict[tuple[str, str], str]) -> str:
    """Substitute generated placeholders with ids from the TARGET workspace.

    Generated pipeline definitions reference items as `@@notebook:name@@`
    rather than by GUID, because GUIDs differ per environment. Resolving here
    keeps one artefact promotable to qa, uat and prod unchanged.

    An unresolved placeholder raises rather than being left in place -- a
    pipeline referencing a literal "@@notebook:x@@" would deploy successfully
    and fail only when someone ran it.
    """
    import re

    kinds = {"notebook": "Notebook", "pipeline": "DataPipeline",
             "lakehouse": "Lakehouse", "warehouse": "Warehouse",
             "environment": "Environment"}

    def sub(match: re.Match) -> str:
        kind, name = match.group(1), match.group(2)
        item_type = kinds[kind]
        item_id = existing.get((name, item_type))
        if item_id is None:
            raise KeyError(
                f"definition references {item_type} {name!r}, which does not "
                f"exist in the target workspace"
            )
        return item_id

    text = text.replace("@@workspace@@", workspace)
    return re.sub(r"@@(notebook|pipeline|lakehouse|warehouse|environment):([^@]+)@@", sub, text)


def pipeline_body(definition_text: str, display_name: str) -> dict:
    return {"definition": {"parts": [
        {"path": "pipeline-content.json",
         "payload": b64(definition_text),
         "payloadType": "InlineBase64"},
        {"path": ".platform",
         "payload": b64(platform_part("DataPipeline", display_name)),
         "payloadType": "InlineBase64"},
    ]}}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--env", default="dev")
    parser.add_argument("--notebooks-dir", default="generated/notebooks")
    parser.add_argument("--pipelines-dir", default="generated/pipelines")
    parser.add_argument("--only", help="push a single item by display name")
    parser.add_argument("--create-missing", action="store_true",
                        help="create an item when no item of that name exists")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    project = Path(args.project).resolve()
    # Resolved through the shared helper so either spec layout works and the
    # migration lives in one place.
    try:
        env = get_environment(project, args.env)
        workspace = get_workspace_id(project, args.env)
    except (FileNotFoundError, KeyError, ValueError) as exc:
        print(f"  ERROR  {exc}")
        return 2

    print(f"project    {project.name}")
    print(f"environment {args.env}  ->  {env.get('workspace')}  ({workspace})")
    print()

    if args.dry_run:
        print("dry run -- nothing will be written")

    fabric = Fabric(get_token())
    existing = {(i["displayName"], i["type"]): i["id"] for i in fabric.list_items(workspace)}
    print(f"{len(existing)} items in the workspace")
    print()

    pushed = skipped = failed = 0

    # ---- notebooks ------------------------------------------------------
    notebook_dir = project / args.notebooks_dir
    for path in sorted(notebook_dir.glob("*.ipynb")):
        name = path.stem
        if args.only and name != args.only:
            continue
        item_id = existing.get((name, "Notebook"))
        if item_id is None:
            if not args.create_missing:
                print(f"  SKIP   {name}  (no Notebook item; --create-missing to add)")
                skipped += 1
                continue
            item_id = fabric.create_item(workspace, name, "Notebook",
                                         "Generated from the project spec set.")
            if item_id is None:
                failed += 1
                continue
            existing[(name, "Notebook")] = item_id
            print(f"  CREATE {name}")
        # Notebooks carry lakehouse-binding placeholders in their metadata, so
        # they go through the same resolution as pipelines. Without it a
        # notebook deploys with a literal "@@lakehouse:lh_bronze@@" as its
        # default and cannot import the framework library.
        try:
            resolved = resolve_placeholders(
                path.read_text(encoding="utf-8"), workspace, existing)
        except KeyError as exc:
            print(f"  FAIL   {name}: {exc}")
            failed += 1
            continue
        if args.dry_run:
            bound = json.loads(resolved).get("metadata", {}) \
                .get("dependencies", {}).get("lakehouse", {}) \
                .get("default_lakehouse_name", "<unbound>")
            print(f"  would push  {name}  ({len(resolved):,} bytes, default={bound})")
            continue
        ok = fabric.update_definition(workspace, "Notebook", item_id,
                                      notebook_body(resolved, name))
        print(f"  {'OK    ' if ok else 'FAIL  '} {name}")
        pushed += ok
        failed += not ok

    # ---- pipelines ------------------------------------------------------
    pipeline_dir = project / args.pipelines_dir
    if pipeline_dir.is_dir():
        for path in sorted(pipeline_dir.glob("*.json")):
            name = path.stem
            if args.only and name != args.only:
                continue
            item_id = existing.get((name, "DataPipeline"))
            if item_id is None:
                if not args.create_missing:
                    print(f"  SKIP   {name}  (no DataPipeline item; --create-missing to add)")
                    skipped += 1
                    continue
                item_id = fabric.create_item(workspace, name, "DataPipeline",
                                             "Generated from the project spec set.")
                if item_id is None:
                    failed += 1
                    continue
                existing[(name, "DataPipeline")] = item_id
                print(f"  CREATE {name}")
            try:
                resolved = resolve_placeholders(
                    path.read_text(encoding="utf-8"), workspace, existing)
            except KeyError as exc:
                print(f"  FAIL   {name}: {exc}")
                failed += 1
                continue
            if args.dry_run:
                references = len(json.loads(resolved)["properties"]["activities"])
                print(f"  would push  {name}  ({references} activities, all refs resolved)")
                continue
            ok = fabric.update_definition(workspace, "DataPipeline", item_id,
                                          pipeline_body(resolved, name))
            print(f"  {'OK    ' if ok else 'FAIL  '} {name}")
            pushed += ok
            failed += not ok

    print()
    print(f"{pushed} pushed, {skipped} skipped, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
