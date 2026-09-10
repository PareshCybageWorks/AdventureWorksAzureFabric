"""
Run named notebooks on demand, and report what each one did.

Why this exists
---------------
A pipeline is all-or-nothing. When one notebook fails you need to re-run THAT
one, not the sixteen beside it -- and on a small or shared capacity, re-running
all sixteen is what caused the failure in the first place:

    TooManyRequestsForCapacity -- HTTP 430

Sixteen notebooks asking for a Livy session at once exceeded a shared FTL64
trial. Running the four that still had work completed first time.

It also reports the failure reason per notebook, which the portal makes you
click through to find.

READ THIS BEFORE DEBUGGING A FAILURE
------------------------------------
Only SOME capacity evictions say so. The rest surface as:

    System cancelled the Spark session due to statement execution failures

which reads exactly like a code defect and is not one. Before investigating,
re-run the notebook ALONE. If it passes, it was contention. That single check
saves a long detour.

If it fails alone, use diagnose_notebook.py -- Fabric will not tell you why
from here.

Usage:
    python run_notebooks.py --project ./03_live_demo --env dev nb_load_incident
    python run_notebooks.py --project ./03_live_demo --env dev nb_a nb_b nb_c
    python run_notebooks.py --project ./03_live_demo --env dev --sequential nb_a nb_b
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "deploy"))
from _project import get_workspace_id                      # noqa: E402

API = "https://api.fabric.microsoft.com/v1"
SCOPE = "https://api.fabric.microsoft.com/.default"
TERMINAL = ("Completed", "Failed", "Cancelled", "Deduped")


def get_token() -> str:
    from azure.identity import AzureCliCredential
    return AzureCliCredential().get_token(SCOPE).token


def notebooks_in(workspace: str, headers: dict) -> dict[str, str]:
    response = requests.get(f"{API}/workspaces/{workspace}/items?type=Notebook",
                            headers=headers, timeout=60, verify=False)
    response.raise_for_status()
    return {i["displayName"]: i["id"] for i in response.json().get("value", [])}


def start(workspace: str, item_id: str, headers: dict) -> str | None:
    response = requests.post(
        f"{API}/workspaces/{workspace}/items/{item_id}/jobs/instances"
        f"?jobType=RunNotebook", headers=headers, json={}, timeout=60, verify=False)
    if response.status_code not in (200, 202):
        return None
    return response.headers.get("Location", "").rsplit("/", 1)[-1] or None


def poll(workspace: str, item_id: str, instance: str, headers: dict) -> dict:
    response = requests.get(
        f"{API}/workspaces/{workspace}/items/{item_id}/jobs/instances/{instance}",
        headers=headers, timeout=60, verify=False)
    return response.json() if response.status_code == 200 else {}


def describe(job: dict) -> str:
    reason = str(job.get("failureReason") or "")
    if not reason:
        return ""
    if "TooManyRequests" in reason or "430" in reason:
        return ("capacity limit (HTTP 430) -- too many concurrent Spark "
                "sessions. Re-run alone before investigating.")
    if "Statements_Failed" in reason or "statement execution failures" in reason:
        return ("session cancelled on a statement failure. This ALSO happens "
                "under capacity pressure -- re-run alone first; if it still "
                "fails, use diagnose_notebook.py.")
    if "PATH_NOT_FOUND" in reason:
        return "landing path does not exist -- the extract produced no file."
    return reason[:240]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--env", default="dev")
    parser.add_argument("--sequential", action="store_true",
                        help="one at a time; use when capacity is tight")
    parser.add_argument("--timeout-minutes", type=int, default=25)
    parser.add_argument("names", nargs="+")
    args = parser.parse_args()

    project = Path(args.project).resolve()
    try:
        workspace = get_workspace_id(project, args.env)
    except (FileNotFoundError, KeyError, ValueError) as exc:
        print(f"  ERROR  {exc}")
        return 2

    headers = {"Authorization": f"Bearer {get_token()}",
               "Content-Type": "application/json"}
    by_name = notebooks_in(workspace, headers)

    batches = [[n] for n in args.names] if args.sequential else [args.names]
    failed = 0

    for batch in batches:
        running: dict[str, tuple[str, str]] = {}
        for name in batch:
            item_id = by_name.get(name)
            if not item_id:
                print(f"  NOT FOUND  {name}")
                failed += 1
                continue
            instance = start(workspace, item_id, headers)
            if not instance:
                print(f"  START FAILED  {name}")
                failed += 1
                continue
            running[name] = (item_id, instance)
            print(f"  started    {name}")

        deadline = time.time() + args.timeout_minutes * 60
        while running and time.time() < deadline:
            time.sleep(20)
            for name, (item_id, instance) in list(running.items()):
                job = poll(workspace, item_id, instance, headers)
                status = job.get("status")
                if status not in TERMINAL:
                    continue
                running.pop(name)
                if status == "Completed":
                    print(f"  OK      {name}")
                else:
                    failed += 1
                    print(f"  FAIL    {name}  ({status})")
                    detail = describe(job)
                    if detail:
                        print(f"          {detail}")

        for name in running:
            print(f"  ....    {name} still running at timeout")
            failed += 1

    print()
    print(f"{len(args.names) - failed} succeeded, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
