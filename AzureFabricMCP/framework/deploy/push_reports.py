"""
Deploy generated PBIR reports to a Fabric workspace.

Creates each report if absent and updates its definition if present.

Rebinding
---------
A report references its semantic model by id, and that id differs in every
environment. The generated PBIR carries `@@modelid@@`, resolved here against the
model deployed in the target workspace -- so one reviewed artefact promotes from
dev to prod unchanged and binds to the right model on arrival.

Deploying a report whose model is absent is refused rather than attempted: it
would publish a report that renders nothing, which looks like a data problem.

Usage:
    python push_reports.py --project ./01_demo-project --env dev
    python push_reports.py --project ./01_demo-project --env dev --dry-run
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--env", default="dev")
    parser.add_argument("--reports", default="generated/reports")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    project = Path(args.project).resolve()
    spec_path = project / "powerbi" / "02-reports.yaml"
    if not spec_path.exists():
        print(f"  ERROR  no reports spec at {spec_path}")
        return 2
    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))

    root = project / args.reports
    if not root.exists():
        print(f"  ERROR  nothing generated at {root}. Run generate_report.py first.")
        return 2

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

    models = requests.get(f"{FABRIC_API}/workspaces/{workspace}/semanticModels",
                          headers=headers, timeout=60, verify=False).json().get("value", [])
    model_ids = {m["displayName"]: m["id"] for m in models}

    existing = requests.get(f"{FABRIC_API}/workspaces/{workspace}/reports",
                            headers=headers, timeout=60, verify=False).json().get("value", [])
    report_ids = {r["displayName"]: r["id"] for r in existing}

    failures = 0
    for report in spec["reports"]:
        name = report["name"]
        display = report.get("display_name", name)
        directory = root / name
        if not directory.exists():
            print(f"  SKIP   {name}: not generated")
            continue

        model_id = model_ids.get(report["model"])
        if not model_id:
            print(f"  ERROR  {name}: semantic model {report['model']!r} is not in "
                  f"this workspace. Deploy it first -- a report bound to a missing "
                  f"model renders empty rather than failing.")
            failures += 1
            continue

        parts = []
        for path in sorted(p for p in directory.rglob("*") if p.is_file()):
            text = path.read_text(encoding="utf-8").replace("@@modelid@@", model_id)
            parts.append({
                "path": str(path.relative_to(directory)).replace("\\", "/"),
                "payload": base64.b64encode(text.encode()).decode(),
                "payloadType": "InlineBase64"})

        pages = len(report["pages"])
        visuals = sum(len(p["visuals"]) for p in report["pages"])
        print(f"  {name}: {pages} pages, {visuals} visuals, {len(parts)} files "
              f"-> {report['model']}")

        if args.dry_run:
            continue

        report_id = report_ids.get(display) or report_ids.get(name)
        if report_id:
            response = requests.post(
                f"{FABRIC_API}/workspaces/{workspace}/reports/{report_id}"
                f"/updateDefinition?updateMetadata=true", headers=headers,
                json={"definition": {"parts": parts}}, timeout=300, verify=False)
        else:
            response = requests.post(
                f"{FABRIC_API}/workspaces/{workspace}/reports", headers=headers,
                json={"displayName": display,
                      "description": " ".join(report.get("description", "").split())[:250],
                      "definition": {"parts": parts}}, timeout=300, verify=False)

        if response.status_code not in (200, 201, 202):
            print(f"  ERROR  {name}: HTTP {response.status_code}")
            print(f"         {response.text[:700]}")
            failures += 1
            continue

        location = response.headers.get("Location")
        status = "Succeeded" if response.status_code in (200, 201) else None
        for _ in range(40):
            if status in ("Succeeded", "Failed"):
                break
            time.sleep(5)
            poll = requests.get(location, headers=headers, timeout=60, verify=False).json()
            status = poll.get("status")
            if status == "Failed":
                print(f"  ERROR  {name}: {poll.get('error', {})}")
                failures += 1
                break

        if status == "Succeeded":
            print(f"  OK     {name} deployed")

    print()
    print(f"{len(spec['reports']) - failures} deployed, {failures} failures")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
