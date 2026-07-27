"""
Deploy a generated TMDL semantic model to a Fabric workspace.

Creates the model if absent and updates its definition if present, so the same
command promotes to dev, qa, uat and prod without a per-environment variant.

Placeholders resolved here
--------------------------
The generated TMDL is environment-neutral and holds two placeholders, which are
substituted against the target workspace at deploy time:

    @@sqlendpoint@@   the warehouse or lakehouse SQL endpoint
    @@database@@      the item the model reads

Resolving at deploy rather than at generation is what lets one reviewed artefact
promote unchanged -- the environment is a property of where it lands, not of
what was built.

Usage:
    python push_semantic_model.py --project ./01_demo-project --env dev
    python push_semantic_model.py --project ./01_demo-project --env dev --dry-run
"""

from __future__ import annotations

import argparse
import base64
import sys
import time
from pathlib import Path

import requests
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _project import get_environment, get_workspace_id  # noqa: E402
import _tsql  # noqa: E402

FABRIC_API = "https://api.fabric.microsoft.com/v1"


def sql_endpoint(headers: dict, workspace: str, item: str, kind: str) -> tuple[str, str] | None:
    """Return (endpoint, database) for the warehouse or lakehouse backing the model."""
    if kind == "warehouse":
        response = requests.get(f"{FABRIC_API}/workspaces/{workspace}/warehouses",
                                headers=headers, timeout=60)
        found = next((w for w in response.json().get("value", [])
                      if w["displayName"] == item), None)
        if not found:
            return None
        return (found.get("properties") or {}).get("connectionString"), item

    response = requests.get(f"{FABRIC_API}/workspaces/{workspace}/lakehouses",
                            headers=headers, timeout=60)
    found = next((l for l in response.json().get("value", [])
                  if l["displayName"] == item), None)
    if not found:
        return None
    endpoint = ((found.get("properties") or {}).get("sqlEndpointProperties") or {})
    # A lakehouse SQL endpoint is provisioned asynchronously and is absent for
    # the first minutes after creation.
    return endpoint.get("connectionString"), endpoint.get("id") or item


def collect(root: Path) -> list[tuple[str, str]]:
    """Every file of the generated model, as (relative path, text)."""
    return sorted(
        (str(p.relative_to(root)).replace("\\", "/"), p.read_text(encoding="utf-8"))
        for p in root.rglob("*") if p.is_file())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--env", default="dev")
    parser.add_argument("--model", default="generated/model")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    project = Path(args.project).resolve()
    model_dir = project / args.model
    if not model_dir.exists():
        print(f"  ERROR  no generated model at {model_dir}. Run generate_tmdl.py first.")
        return 2

    spec_path = project / "powerbi" / "01-semantic-model.yaml"
    if not spec_path.exists():
        print(f"  ERROR  no semantic model spec at {spec_path}")
        return 2
    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    name = spec["model"]["name"]
    source = spec["model"]["source"]

    try:
        environment = get_environment(project, args.env)
        workspace = get_workspace_id(project, args.env)
    except (FileNotFoundError, KeyError, ValueError) as exc:
        print(f"  ERROR  {exc}")
        return 2

    print(f"environment {args.env}  ->  {environment.get('workspace')}  ({workspace})")

    cred = _tsql.credential()
    headers = {"Authorization": f"Bearer {cred.get_token(_tsql.FABRIC_SCOPE).token}",
               "Content-Type": "application/json"}

    resolved = sql_endpoint(headers, workspace, source["item"], source["kind"])
    if not resolved or not resolved[0]:
        print(f"  ERROR  no SQL endpoint for {source['kind']} {source['item']!r}. "
              f"A lakehouse endpoint takes a few minutes to appear after creation.")
        return 2
    endpoint, database = resolved
    print(f"binding to {source['item']}  ({endpoint})")

    files = collect(model_dir)
    parts = []
    for path, text in files:
        text = text.replace("@@sqlendpoint@@", endpoint).replace("@@database@@", database)
        parts.append({"path": path,
                      "payload": base64.b64encode(text.encode()).decode(),
                      "payloadType": "InlineBase64"})

    print(f"{len(parts)} files, storage mode {spec['model']['storage_mode']}")
    if args.dry_run:
        for path, _ in files:
            print(f"    {path}")
        print("\ndry run -- nothing deployed")
        return 0

    existing = requests.get(f"{FABRIC_API}/workspaces/{workspace}/semanticModels",
                            headers=headers, timeout=60).json().get("value", [])
    found = next((m for m in existing if m["displayName"] == name), None)

    if found:
        print(f"updating {name}")
        response = requests.post(
            f"{FABRIC_API}/workspaces/{workspace}/semanticModels/{found['id']}"
            f"/updateDefinition?updateMetadata=true", headers=headers,
            json={"definition": {"parts": parts}}, timeout=300)
    else:
        print(f"creating {name}")
        response = requests.post(
            f"{FABRIC_API}/workspaces/{workspace}/semanticModels", headers=headers,
            json={"displayName": name,
                  "description": " ".join(spec["model"].get("description", "").split())[:250],
                  "definition": {"parts": parts}}, timeout=300)

    if response.status_code not in (200, 201, 202):
        print(f"  ERROR  HTTP {response.status_code}")
        print(f"         {response.text[:900]}")
        return 1

    # A long-running create returns 202 with a poll location.
    location = response.headers.get("Location")
    status = "Succeeded" if response.status_code in (200, 201) else None
    for _ in range(40):
        if status in ("Succeeded", "Failed"):
            break
        time.sleep(5)
        poll = requests.get(location, headers=headers, timeout=60).json()
        status = poll.get("status")
        if status == "Failed":
            print(f"  ERROR  {poll.get('error', {})}")
            return 1

    print(f"  OK     {name} deployed ({status})")
    print()
    print("The model reads DirectLake from the warehouse: no refresh to schedule,")
    print("no import to age. Reports bind to this model, never to wh_gold.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
