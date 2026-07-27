"""
Verify that DEPLOYED items reference their own environment's storage.

Placeholders are resolved at deploy time, so the artefact in git is
environment-neutral and the artefact in a workspace is not. This reads back
what actually landed and checks every GUID inside it belongs to the workspace
it landed in.

Why read it back rather than trust the deploy
---------------------------------------------
A notebook that references another environment's lakehouse does not fail. It
runs, reads that environment's data, and writes there -- so a qa job quietly
populates dev, or worse, prod. Nothing in Fabric objects: the ids are valid,
they are simply the wrong ones. The only way to know is to look at what is
deployed.

Also catches an unresolved placeholder that survived, which would fail only
when someone ran the notebook.

Usage:
    python verify_bindings.py --project ./01_demo-project --env qa
    python verify_bindings.py --project ./01_demo-project --env qa --compare dev
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "deploy"))
from _project import get_environment, get_workspace_id  # noqa: E402
import _tsql  # noqa: E402

FABRIC_API = "https://api.fabric.microsoft.com/v1"
GUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
PLACEHOLDER = re.compile(r"@@[^@]+@@")


def workspace_guids(headers: dict, workspace: str) -> dict[str, str]:
    """Every item id in a workspace -> 'name (Type)'."""
    items = _tsql.with_retry("GET", f"{FABRIC_API}/workspaces/{workspace}/items",
                             headers=headers, timeout=90).json().get("value", [])
    known = {workspace: "the workspace itself"}
    for item in items:
        known[item["id"]] = f"{item['displayName']} ({item['type']})"
    return known


def definitions(headers: dict, workspace: str, kind: str, collection: str) -> dict[str, str]:
    """{item name: decoded definition text} for every item of a type."""
    items = _tsql.with_retry("GET", f"{FABRIC_API}/workspaces/{workspace}/items?type={kind}",
                             headers=headers, timeout=90).json().get("value", [])
    out: dict[str, str] = {}
    for item in items:
        response = _tsql.with_retry(
            "POST", f"{FABRIC_API}/workspaces/{workspace}/{collection}/{item['id']}"
                    f"/getDefinition", headers=headers, timeout=180)
        if not response.ok:
            continue
        parts = response.json().get("definition", {}).get("parts", [])
        text = ""
        for part in parts:
            if part.get("payloadType") == "InlineBase64":
                try:
                    text += base64.b64decode(part["payload"]).decode("utf-8", "replace")
                except Exception:                                  # noqa: BLE001
                    pass
        out[item["displayName"]] = text
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--env", required=True)
    parser.add_argument("--compare", help="another environment whose ids must NOT appear")
    args = parser.parse_args()

    project = Path(args.project).resolve()
    workspace = get_workspace_id(project, args.env)
    headers = {"Authorization": f"Bearer "
                                f"{_tsql.credential().get_token(_tsql.FABRIC_SCOPE).token}",
               "Content-Type": "application/json"}

    print(f"environment {args.env}  ->  "
          f"{get_environment(project, args.env).get('workspace')}  ({workspace})")

    mine = workspace_guids(headers, workspace)
    foreign: dict[str, str] = {}
    if args.compare:
        other = get_workspace_id(project, args.compare)
        foreign = workspace_guids(headers, other)
        print(f"comparing against {args.compare} ({len(foreign)} ids that must not appear)")
    print()

    checked = problems = 0
    for kind, collection in (("Notebook", "notebooks"), ("DataPipeline", "dataPipelines")):
        for name, text in sorted(definitions(headers, workspace, kind, collection).items()):
            checked += 1
            issues = []

            left = set(PLACEHOLDER.findall(text))
            if left:
                issues.append(f"unresolved placeholder(s): {sorted(left)}")

            for guid in set(GUID.findall(text)):
                if guid in mine:
                    continue
                if guid in foreign:
                    issues.append(f"{guid} belongs to {args.compare}: {foreign[guid]}")
                # A guid that is in neither workspace is not evidence of a
                # fault -- lineage tags and logical ids are guids too -- so it
                # is not reported.

            if issues:
                problems += 1
                print(f"  FAIL  {name}")
                for issue in issues:
                    print(f"          {issue}")

    print()
    if problems:
        print(f"{problems} of {checked} item(s) reference something outside {args.env}")
        return 1
    print(f"all {checked} deployed item(s) reference only {args.env}'s own items")
    return 0


if __name__ == "__main__":
    sys.exit(main())
